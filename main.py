import os
import io
import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pypdf import PdfReader
import psycopg2
from psycopg2.extras import RealDictCursor
import edge_tts
import httpx

# ====================== LOGGING ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="lucsi API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====================== CONFIG ======================
DATABASE_URL = os.getenv("DATABASE_URL")
GROK_API_KEY = os.getenv("GROK_API_KEY")
GROK_API_URL = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-4.6"

# ====================== BASE DE DATOS ======================
def get_connection():
    if not DATABASE_URL:
        raise Exception("No se encontró la variable de entorno DATABASE_URL")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Tabla de documentos (con tipo)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL,
            content BYTEA NOT NULL,
            full_text TEXT,
            size_bytes INTEGER,
            pages INTEGER,
            doc_type TEXT DEFAULT 'text',          -- 'text' o 'exercise'
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Añadir columna doc_type si no existe
    cur.execute("""
        DO $$ 
        BEGIN 
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='documents' AND column_name='doc_type'
            ) THEN
                ALTER TABLE documents ADD COLUMN doc_type TEXT DEFAULT 'text';
            END IF;
        END $$;
    """)

    # Tabla de ejercicios individuales
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exercises (
            id SERIAL PRIMARY KEY,
            document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            exercise_number INTEGER NOT NULL,
            statement TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ Tablas listas (documents + exercises)")


@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:
        logger.error(f"❌ Error al inicializar DB: {e}")


# ====================== MODELOS ======================
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    question: str
    full_text: str
    history: Optional[List[ChatMessage]] = []

class RenameRequest(BaseModel):
    name: str

class GenerateExercisesRequest(BaseModel):
    source_document_id: int                 # documento de tipo 'text'
    reference_document_id: Optional[int] = None  # opcional, tipo 'exercise'


# ====================== UTILIDADES ======================
async def call_grok(messages: list, temperature: float = 0.3) -> str:
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="GROK_API_KEY no configurada")

    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            GROK_API_URL,
            headers={
                "Authorization": f"Bearer {GROK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": GROK_MODEL,
                "messages": messages,
                "temperature": temperature
            }
        )

    if response.status_code != 200:
        logger.error(f"Error Grok: {response.status_code} - {response.text}")
        raise HTTPException(status_code=500, detail="Error al contactar con Grok")

    data = response.json()
    return data["choices"][0]["message"]["content"]


def extract_text_from_pdf(contents: bytes) -> str:
    pdf_file = io.BytesIO(contents)
    reader = PdfReader(pdf_file)
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n\n".join(parts).strip()


# ====================== ENDPOINTS DOCUMENTOS (comunes) ======================

@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    name: str = Form(...),
    doc_type: str = Form("text")           # 'text' o 'exercise'
):
    logger.info("=" * 70)
    logger.info(f"🚀 SUBIDA DE DOCUMENTO (tipo: {doc_type})")

    if doc_type not in ("text", "exercise"):
        raise HTTPException(status_code=400, detail="doc_type debe ser 'text' o 'exercise'")

    try:
        if file.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Solo se permiten archivos PDF")

        final_name = name.strip()
        if not final_name:
            raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")

        contents = await file.read()
        size_bytes = len(contents)
        full_text = extract_text_from_pdf(contents)
        num_pages = len(PdfReader(io.BytesIO(contents)).pages)

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO documents (filename, content, full_text, size_bytes, pages, doc_type)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, filename, size_bytes, pages, doc_type, uploaded_at;
        """, (final_name, contents, full_text, size_bytes, num_pages, doc_type))

        saved = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"✅ Documento guardado: {saved['filename']} (ID {saved['id']}) tipo={doc_type}")

        return {
            "status": "success",
            "document": {
                "id": saved["id"],
                "filename": saved["filename"],
                "size_bytes": saved["size_bytes"],
                "pages": saved["pages"],
                "doc_type": saved["doc_type"],
                "uploaded_at": str(saved["uploaded_at"])
            }
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents")
async def list_documents(doc_type: Optional[str] = None):
    try:
        conn = get_connection()
        cur = conn.cursor()

        if doc_type:
            cur.execute("""
                SELECT id, filename, size_bytes, pages, doc_type, uploaded_at
                FROM documents
                WHERE doc_type = %s
                ORDER BY uploaded_at DESC;
            """, (doc_type,))
        else:
            cur.execute("""
                SELECT id, filename, size_bytes, pages, doc_type, uploaded_at
                FROM documents
                ORDER BY uploaded_at DESC;
            """)

        rows = cur.fetchall()
        cur.close()
        conn.close()

        documents = [{
            "id": row["id"],
            "title": row["filename"],
            "date": row["uploaded_at"].strftime("%d %b %Y") if row["uploaded_at"] else "",
            "pages": row["pages"],
            "size_bytes": row["size_bytes"],
            "doc_type": row["doc_type"]
        } for row in rows]

        return {"documents": documents}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: int):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, filename, full_text, size_bytes, pages, doc_type, uploaded_at
            FROM documents WHERE id = %s;
        """, (doc_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Documento no encontrado")

        return {
            "id": row["id"],
            "title": row["filename"],
            "full_text": row["full_text"] or "",
            "pages": row["pages"],
            "size_bytes": row["size_bytes"],
            "doc_type": row["doc_type"],
            "uploaded_at": str(row["uploaded_at"])
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: int):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM documents WHERE id = %s RETURNING id, filename;", (doc_id,))
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not deleted:
            raise HTTPException(status_code=404, detail="Documento no encontrado")

        logger.info(f"🗑️ Documento eliminado: {deleted['filename']}")
        return {"status": "success", "message": "Documento eliminado"}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/documents/{doc_id}/rename")
async def rename_document(doc_id: int, request: RenameRequest):
    new_name = request.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE documents SET filename = %s
            WHERE id = %s
            RETURNING id, filename;
        """, (new_name, doc_id))
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not updated:
            raise HTTPException(status_code=404, detail="Documento no encontrado")

        return {
            "status": "success",
            "document": {"id": updated["id"], "title": updated["filename"]}
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== MODO EJERCICIOS ======================

@app.post("/api/exercises/process")
async def process_exercise_document(document_id: int = Form(...)):
    """
    Toma un documento de tipo 'exercise', lo envía a Grok para extraer
    los ejercicios individuales y los guarda en la tabla exercises.
    """
    logger.info(f"📝 Procesando documento de ejercicios ID {document_id}")

    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("SELECT id, full_text, doc_type FROM documents WHERE id = %s", (document_id,))
        doc = cur.fetchone()

        if not doc:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        if doc["doc_type"] != "exercise":
            raise HTTPException(status_code=400, detail="El documento no es de tipo exercise")

        full_text = doc["full_text"] or ""

        # Prompt reforzado + detección de ejercicios válidos
        system_prompt = """
Eres un experto en educación. Tu ÚNICA tarea es extraer y formatear ejercicios educativos a partir del texto que te den.

REGLAS ESTRICTAS:
1. Solo procesas contenido educativo legítimo (matemáticas, física, química, biología, lengua, historia, etc.).
2. Si el texto NO contiene ejercicios claros o no es material educativo, responde EXACTAMENTE con:
   {"error": "no_exercises_found"}
3. Si el contenido es inapropiado, spam o no relacionado con enseñanza, responde EXACTAMENTE con:
   {"error": "invalid_content"}
4. Si hay ejercicios válidos, responde SOLO con un JSON válido de esta forma:
{
  "exercises": [
    {"number": 1, "statement": "enunciado completo del ejercicio 1"},
    {"number": 2, "statement": "enunciado completo del ejercicio 2"}
  ]
}
No añadas ninguna explicación fuera del JSON.
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Extrae los ejercicios de este documento:\n\n{full_text}"}
        ]

        response_text = await call_grok(messages, temperature=0.2)

        # Intentar parsear la respuesta
        try:
            data = json.loads(response_text.strip())
        except:
            # Si Grok no devolvió JSON limpio, forzar error
            raise HTTPException(status_code=422, detail="no_exercises_found")

        if "error" in data:
            error_type = data["error"]
            if error_type == "no_exercises_found":
                raise HTTPException(status_code=422, detail="no_exercises_found")
            else:
                raise HTTPException(status_code=422, detail="invalid_content")

        exercises = data.get("exercises", [])
        if not exercises:
            raise HTTPException(status_code=422, detail="no_exercises_found")

        # Borrar ejercicios anteriores de este documento (por si se reprocesa)
        cur.execute("DELETE FROM exercises WHERE document_id = %s", (document_id,))

        # Guardar los nuevos
        for ex in exercises:
            cur.execute("""
                INSERT INTO exercises (document_id, exercise_number, statement)
                VALUES (%s, %s, %s)
            """, (document_id, ex["number"], ex["statement"]))

        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"✅ {len(exercises)} ejercicios extraídos y guardados")

        return {
            "status": "success",
            "count": len(exercises),
            "exercises": exercises
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Error procesando ejercicios: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/exercises/generate")
async def generate_exercises(request: GenerateExercisesRequest):
    """
    Genera nuevos ejercicios a partir de un documento de tipo 'text'
    (y opcionalmente un documento de referencia de tipo 'exercise').
    """
    logger.info(f"🧠 Generando ejercicios desde documento {request.source_document_id}")

    try:
        conn = get_connection()
        cur = conn.cursor()

        # Documento fuente (debe ser 'text')
        cur.execute("SELECT id, full_text, doc_type FROM documents WHERE id = %s", (request.source_document_id,))
        source = cur.fetchone()
        if not source or source["doc_type"] != "text":
            raise HTTPException(status_code=400, detail="El documento fuente debe ser de tipo text")

        reference_text = ""
        if request.reference_document_id:
            cur.execute("SELECT full_text, doc_type FROM documents WHERE id = %s", (request.reference_document_id,))
            ref = cur.fetchone()
            if ref and ref["doc_type"] == "exercise":
                reference_text = ref["full_text"] or ""

        system_prompt = """
Eres un profesor experto. Tu tarea es generar ejercicios educativos de calidad a partir del texto que te proporcionen.

REGLAS ESTRICTAS:
1. Solo generas ejercicios educativos legítimos.
2. Si el texto no es adecuado para generar ejercicios (no es material de estudio, es inapropiado, spam, etc.), responde EXACTAMENTE:
   {"error": "invalid_content"}
3. Si todo es correcto, responde SOLO con un JSON válido:
{
  "exercises": [
    {"number": 1, "statement": "enunciado del ejercicio 1"},
    {"number": 2, "statement": "enunciado del ejercicio 2"}
  ]
}
Genera entre 4 y 8 ejercicios de dificultad progresiva.
No añadas ninguna explicación fuera del JSON.
"""

        user_content = f"Texto base para generar ejercicios:\n\n{source['full_text']}"
        if reference_text:
            user_content += f"\n\n---\nEjercicios de referencia (estilo a seguir):\n\n{reference_text}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        response_text = await call_grok(messages, temperature=0.4)

        try:
            data = json.loads(response_text.strip())
        except:
            raise HTTPException(status_code=422, detail="invalid_content")

        if "error" in data:
            raise HTTPException(status_code=422, detail=data["error"])

        exercises = data.get("exercises", [])
        if not exercises:
            raise HTTPException(status_code=422, detail="no_exercises_found")

        # Crear un nuevo documento de tipo 'exercise' para guardar estos ejercicios
        new_name = f"Ejercicios generados - {datetime.now().strftime('%d/%m/%Y %H:%M')}"

        cur.execute("""
            INSERT INTO documents (filename, content, full_text, size_bytes, pages, doc_type)
            VALUES (%s, %s, %s, %s, %s, 'exercise')
            RETURNING id;
        """, (new_name, b"", "\n\n".join([e["statement"] for e in exercises]), 0, 1))

        new_doc_id = cur.fetchone()["id"]

        for ex in exercises:
            cur.execute("""
                INSERT INTO exercises (document_id, exercise_number, statement)
                VALUES (%s, %s, %s)
            """, (new_doc_id, ex["number"], ex["statement"]))

        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"✅ {len(exercises)} ejercicios generados y guardados en documento {new_doc_id}")

        return {
            "status": "success",
            "document_id": new_doc_id,
            "count": len(exercises),
            "exercises": exercises
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Error generando ejercicios: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/exercises/{document_id}")
async def list_exercises(document_id: int):
    """Devuelve todos los ejercicios de un documento de tipo exercise"""
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, exercise_number, statement
            FROM exercises
            WHERE document_id = %s
            ORDER BY exercise_number ASC;
        """, (document_id,))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        exercises = [{
            "id": row["id"],
            "number": row["exercise_number"],
            "statement": row["statement"]
        } for row in rows]

        return {"exercises": exercises}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/exercises/{document_id}/{exercise_number}")
async def get_exercise(document_id: int, exercise_number: int):
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, exercise_number, statement
            FROM exercises
            WHERE document_id = %s AND exercise_number = %s;
        """, (document_id, exercise_number))

        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Ejercicio no encontrado")

        return {
            "id": row["id"],
            "number": row["exercise_number"],
            "statement": row["statement"]
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== TTS y CHAT (se mantienen) ======================

@app.post("/api/tts")
async def text_to_speech(
    text: str = Form(...),
    voice: str = Form("es-ES-AlvaroNeural")
):
    if not text.strip():
        raise HTTPException(status_code=400, detail="El texto no puede estar vacío")

    try:
        communicate = edge_tts.Communicate(text=text.strip(), voice=voice)
        audio_buffer = io.BytesIO()

        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.write(chunk["data"])

        audio_buffer.seek(0)
        return StreamingResponse(
            audio_buffer,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=speech.mp3"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat")
async def chat_with_grok(request: ChatRequest):
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="GROK_API_KEY no configurada")

    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "Eres un profesor experto, claro y paciente. "
                    "Responde de forma didáctica basándote en el contenido proporcionado.\n\n"
                    "=== CONTENIDO ===\n"
                    f"{request.full_text}\n"
                    "=== FIN ==="
                )
            }
        ]

        for msg in request.history:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": request.question})

        answer = await call_grok(messages, temperature=0.5)
        return {"answer": answer}

    except Exception as e:
        logger.error(f"❌ Error en chat: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
