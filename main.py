import os
import io
import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
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

MAX_PAGES_EXERCISES = 10

# ====================== BASE DE DATOS ======================
def get_connection():
    if not DATABASE_URL:
        raise Exception("No se encontró la variable de entorno DATABASE_URL")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL,
            content BYTEA NOT NULL,
            full_text TEXT,
            size_bytes INTEGER,
            pages INTEGER,
            doc_type TEXT DEFAULT 'text',
            start_page INTEGER DEFAULT 1,
            end_page INTEGER,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    for col, definition in [
        ("doc_type", "TEXT DEFAULT 'text'"),
        ("start_page", "INTEGER DEFAULT 1"),
        ("end_page", "INTEGER")
    ]:
        cur.execute(f"""
            DO $$ 
            BEGIN 
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='documents' AND column_name='{col}'
                ) THEN
                    ALTER TABLE documents ADD COLUMN {col} {definition};
                END IF;
            END $$;
        """)

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
    logger.info("✅ Tablas listas")


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
    mode: Optional[str] = "doubt"          # "doubt" | "step" | "solution"

class RenameRequest(BaseModel):
    name: str

class GenerateExercisesRequest(BaseModel):
    source_document_id: Optional[int] = None
    reference_document_id: Optional[int] = None


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


def extract_text_from_pdf(contents: bytes, start_page: int = 1, end_page: int = None) -> tuple[str, int]:
    pdf_file = io.BytesIO(contents)
    reader = PdfReader(pdf_file)
    total_pages = len(reader.pages)

    start_page = max(1, start_page)
    if end_page is None:
        end_page = total_pages
    end_page = min(total_pages, end_page)

    if start_page > end_page:
        raise HTTPException(status_code=400, detail="La página de inicio no puede ser mayor que la final")

    pages_count = end_page - start_page + 1

    parts = []
    for i in range(start_page - 1, end_page):
        parts.append(reader.pages[i].extract_text() or "")

    return "\n\n".join(parts).strip(), pages_count


# ====================== ENDPOINTS DOCUMENTOS ======================

@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    name: str = Form(...),
    doc_type: str = Form("text"),
    start_page: int = Form(1),
    end_page: int = Form(None)
):
    logger.info(f"🚀 SUBIDA → tipo={doc_type} | páginas {start_page}-{end_page}")

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

        pdf_reader = PdfReader(io.BytesIO(contents))
        total_pages = len(pdf_reader.pages)

        if end_page is None:
            end_page = total_pages

        # Solo limitamos páginas en modo exercise
        if doc_type == "exercise":
            pages_requested = end_page - start_page + 1
            if pages_requested > MAX_PAGES_EXERCISES:
                raise HTTPException(
                    status_code=400,
                    detail=f"En modo ejercicios el máximo es {MAX_PAGES_EXERCISES} páginas"
                )
            full_text, extracted_pages = extract_text_from_pdf(contents, start_page, end_page)
        else:
            # Modo lectura → documento completo
            full_text, extracted_pages = extract_text_from_pdf(contents, 1, total_pages)
            start_page = 1
            end_page = total_pages

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO documents 
                (filename, content, full_text, size_bytes, pages, doc_type, start_page, end_page)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, filename, size_bytes, pages, doc_type, start_page, end_page, uploaded_at;
        """, (final_name, contents, full_text, size_bytes, extracted_pages, doc_type, start_page, end_page))

        saved = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"✅ Guardado: {saved['filename']} (ID {saved['id']}) | {extracted_pages} páginas")

        return {
            "status": "success",
            "document": {
                "id": saved["id"],
                "filename": saved["filename"],
                "size_bytes": saved["size_bytes"],
                "pages": saved["pages"],
                "doc_type": saved["doc_type"],
                "start_page": saved["start_page"],
                "end_page": saved["end_page"],
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
                SELECT id, filename, size_bytes, pages, doc_type, start_page, end_page, uploaded_at
                FROM documents WHERE doc_type = %s ORDER BY uploaded_at DESC;
            """, (doc_type,))
        else:
            cur.execute("""
                SELECT id, filename, size_bytes, pages, doc_type, start_page, end_page, uploaded_at
                FROM documents ORDER BY uploaded_at DESC;
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
            "doc_type": row["doc_type"],
            "start_page": row["start_page"],
            "end_page": row["end_page"]
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
            SELECT id, filename, full_text, size_bytes, pages, doc_type, 
                   start_page, end_page, uploaded_at
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
            "start_page": row["start_page"],
            "end_page": row["end_page"],
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

        logger.info(f"🗑️ Eliminado: {deleted['filename']}")
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
            UPDATE documents SET filename = %s WHERE id = %s
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
    logger.info(f"📝 Procesando ejercicios del documento {document_id}")

    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("SELECT id, full_text, doc_type, pages FROM documents WHERE id = %s", (document_id,))
        doc = cur.fetchone()

        if not doc:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        if doc["doc_type"] != "exercise":
            raise HTTPException(status_code=400, detail="El documento no es de tipo exercise")

        full_text = doc["full_text"] or ""

        system_prompt = """
Eres un experto en educación. Tu ÚNICA tarea es extraer SOLO enunciados completos y reales de ejercicios.

REGLAS MUY ESTRICTAS:
1. Un enunciado válido debe ser un problema o pregunta completa que el alumno tenga que resolver (cálculo, demostración, explicación, etc.).
2. IGNORA completamente:
   - Títulos de temas o apartados
   - Introducciones y explicaciones teóricas
   - Definiciones
   - Instrucciones generales (“Resuelve los siguientes ejercicios…”)
   - Fragmentos que solo parecen ejercicios cuando se leen aislados pero en contexto más amplio no lo son
3. Analiza siempre el contexto amplio (varias líneas antes y después) para decidir si algo es realmente un enunciado completo.
4. Si no hay ningún ejercicio claro → responde EXACTAMENTE:
   {"error": "no_exercises_found"}
5. Si el contenido no es educativo → responde EXACTAMENTE:
   {"error": "invalid_content"}
6. Si hay ejercicios válidos, responde SOLO con este JSON (sin texto adicional):
{
  "exercises": [
    {"number": 1, "statement": "enunciado completo y limpio"},
    {"number": 2, "statement": "enunciado completo y limpio"}
  ]
}
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Analiza el siguiente texto con contexto amplio y extrae únicamente los enunciados de ejercicios reales y completos:\n\n{full_text}"}
        ]

        response_text = await call_grok(messages, temperature=0.1)

        try:
            data = json.loads(response_text.strip())
        except:
            raise HTTPException(status_code=422, detail="no_exercises_found")

        if "error" in data:
            raise HTTPException(status_code=422, detail=data["error"])

        exercises = data.get("exercises", [])
        if not exercises:
            raise HTTPException(status_code=422, detail="no_exercises_found")

        cur.execute("DELETE FROM exercises WHERE document_id = %s", (document_id,))

        for ex in exercises:
            cur.execute("""
                INSERT INTO exercises (document_id, exercise_number, statement)
                VALUES (%s, %s, %s)
            """, (document_id, ex["number"], ex["statement"]))

        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"✅ {len(exercises)} ejercicios reales guardados")
        return {"status": "success", "count": len(exercises), "exercises": exercises}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/exercises/{document_id}")
async def list_exercises(document_id: int):
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

        return {
            "exercises": [{
                "id": row["id"],
                "number": row["exercise_number"],
                "statement": row["statement"]
            } for row in rows]
        }
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


# ====================== TTS ======================
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


# ====================== CHAT ======================
@app.post("/api/chat")
async def chat_with_grok(request: ChatRequest):
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="GROK_API_KEY no configurada")

    try:
        mode = request.mode or "doubt"

        if mode == "step":
            system_content = (
                "Eres un profesor paciente, claro y didáctico. Estás resolviendo un ejercicio paso a paso.\n\n"
                "REGLAS OBLIGATORIAS:\n"
                "1. NUNCA empieces con 'Lee el enunciado' ni nada similar. El alumno ya lo tiene delante.\n"
                "2. Empieza siempre con una explicación intuitiva y sencilla del enfoque, y después ve detallando la técnica.\n"
                "3. Da SOLO un paso cada vez.\n"
                "4. Después de cada paso pregunta exactamente:\n"
                "   \"¿Tienes alguna duda sobre este paso o quieres que continúe la explicación?\"\n"
                "5. Cuando hayas terminado toda la resolución di claramente:\n"
                "   \"He terminado la resolución del ejercicio.\"\n"
                "6. Usa texto limpio. No uses markdown de negrita (**texto**), ni guiones innecesarios, ni caracteres especiales que puedan verse mal.\n"
                "7. Sé natural y conversacional.\n\n"
                "=== EJERCICIO ===\n"
                f"{request.full_text}\n"
                "=== FIN ==="
            )
        elif mode == "solution":
            system_content = (
                "Eres un profesor que corrige la solución de un alumno.\n"
                "Explica los errores y aciertos de forma clara y didáctica.\n"
                "Después de cada observación pregunta si quiere continuar.\n"
                "Usa texto limpio, sin markdown de negrita ni guiones innecesarios.\n\n"
                "=== EJERCICIO ===\n"
                f"{request.full_text}\n"
                "=== FIN ==="
            )
        else:  # doubt
            system_content = (
                "Eres un profesor experto, claro y paciente.\n"
                "El alumno tiene una duda relacionada con el ejercicio.\n"
                "Puedes explicar conceptos, métodos, fórmulas o cualquier duda relacionada "
                "con el contenido del problema, aunque no esté literalmente en el enunciado.\n"
                "Empieza con ideas intuitivas y después profundiza si es necesario.\n"
                "Usa texto limpio, sin markdown de negrita (**texto**) ni guiones innecesarios.\n\n"
                "=== EJERCICIO ===\n"
                f"{request.full_text}\n"
                "=== FIN ==="
            )

        messages = [{"role": "system", "content": system_content}]

        for msg in request.history:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": request.question})

        answer = await call_grok(messages, temperature=0.45)
        return {"answer": answer}

    except Exception as e:
        logger.error(f"❌ Error en chat: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
