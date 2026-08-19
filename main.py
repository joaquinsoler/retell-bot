import os
import io
import json
import logging
import re
from datetime import datetime
from typing import List, Optional
from contextlib import contextmanager

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from pypdf import PdfReader
import psycopg2
from psycopg2.extras import RealDictCursor
import edge_tts
import httpx
import traceback  # ← añade esto arriba

async def call_grok(messages: list, temperature: float = 0.3, timeout: float = 180.0) -> str:
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="Error de configuración del servidor")

    async with httpx.AsyncClient(timeout=timeout) as client:
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
        logger.error(f"Error Grok: {response.status_code} - {response.text[:800]}")
        raise HTTPException(status_code=500, detail="Error al contactar con el asistente")

    data = response.json()
    return data["choices"][0]["message"]["content"]

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
MAX_TEXT_CHARS = 120000  # Seguro para el contexto de Grok 4.6


# ====================== BASE DE DATOS ======================
@contextmanager
def get_db_connection():
    if not DATABASE_URL:
        raise Exception("No se encontró la variable de entorno DATABASE_URL")

    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        yield conn
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def init_db():
    with get_db_connection() as conn:
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
            CREATE INDEX IF NOT EXISTS idx_documents_doc_type
            ON documents(doc_type);
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

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_exercises_document_id
            ON exercises(document_id);
        """)

        cur.close()
        logger.info("✅ Tablas e índices listos")


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
    mode: Optional[str] = "doubt"
    image_base64: Optional[str] = None
    image_mime: Optional[str] = None


class RenameRequest(BaseModel):
    name: str


class GenerateExercisesRequest(BaseModel):
    source_document_id: Optional[int] = None
    reference_document_id: Optional[int] = None


# ====================== UTILIDADES ======================
def sanitize_text_for_prompt(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"={3,}.*?={3,}", "[contenido eliminado]", text, flags=re.DOTALL)
    text = text.replace("=== FIN ===", "[fin]")
    text = text.replace("=== CONTENIDO ===", "[contenido]")
    return text.strip()


def truncate_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars] + "\n\n[... texto truncado por longitud ...]"


def extract_json_from_response(text: str) -> dict:
    text = text.strip()

    # 1. Parseo directo
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. Buscar bloque { ... }
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    # 3. Limpiar markdown
    cleaned = re.sub(r'```json|```', '', text).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 4. Último intento: desde el primer { hasta el último }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass

    raise ValueError("No se pudo extraer un JSON válido de la respuesta de Grok")


async def call_grok(messages: list, temperature: float = 0.3, timeout: float = 180.0) -> str:
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="Error de configuración del servidor")

    async with httpx.AsyncClient(timeout=timeout) as client:
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
        logger.error(f"Error Grok: {response.status_code} - {response.text[:800]}")
        raise HTTPException(status_code=500, detail="Error al contactar con el asistente")

    data = response.json()
    return data["choices"][0]["message"]["content"]


def extract_text_from_pdf(contents: bytes, start_page: int = 1, end_page: Optional[int] = None) -> tuple[str, int]:
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


# ====================== HEALTH ======================
@app.get("/health")
async def health():
    return {"status": "ok", "service": "lucsi"}


# ====================== ENDPOINTS DOCUMENTOS ======================

@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    name: str = Form(...),
    doc_type: str = Form("text"),
    start_page: int = Form(1),
    end_page: Optional[int] = Form(None)
):
    logger.info(f"🚀 SUBIDA → tipo={doc_type} | páginas {start_page}-{end_page}")

    if doc_type not in ("text", "exercise"):
        raise HTTPException(status_code=400, detail="Tipo de documento no válido")

    try:
        content_type = (file.content_type or "").lower()
        valid_mimes = ["application/pdf", "application/x-pdf", "application/octet-stream"]
        if content_type not in valid_mimes and not (file.filename or "").lower().endswith(".pdf"):
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

        if doc_type == "exercise":
            pages_requested = end_page - start_page + 1
            if pages_requested > MAX_PAGES_EXERCISES:
                raise HTTPException(
                    status_code=400,
                    detail=f"En modo ejercicios el máximo permitido es {MAX_PAGES_EXERCISES} páginas"
                )
            full_text, extracted_pages = extract_text_from_pdf(contents, start_page, end_page)
        else:
            full_text, extracted_pages = extract_text_from_pdf(contents, 1, total_pages)
            start_page = 1
            end_page = total_pages

        def _save():
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO documents
                        (filename, content, full_text, size_bytes, pages, doc_type, start_page, end_page)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, filename, size_bytes, pages, doc_type, start_page, end_page, uploaded_at;
                """, (final_name, contents, full_text, size_bytes, extracted_pages, doc_type, start_page, end_page))
                saved = cur.fetchone()
                cur.close()
                return saved

        saved = await run_in_threadpool(_save)

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ ERROR en upload: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al procesar el documento")


@app.get("/api/documents")
async def list_documents(doc_type: Optional[str] = None):
    def _list():
        with get_db_connection() as conn:
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
            return rows

    try:
        rows = await run_in_threadpool(_list)
        documents = [{
            "id": row["id"],
            "title": row["filename"],
            "date": row["uploaded_at"].strftime("%d/%m/%Y") if row["uploaded_at"] else "",
            "pages": row["pages"],
            "size_bytes": row["size_bytes"],
            "doc_type": row["doc_type"],
            "start_page": row["start_page"],
            "end_page": row["end_page"]
        } for row in rows]
        return {"documents": documents}
    except Exception as e:
        logger.error(f"Error listando documentos: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener los documentos")


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: int):
    def _get():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, filename, full_text, size_bytes, pages, doc_type,
                       start_page, end_page, uploaded_at
                FROM documents WHERE id = %s;
            """, (doc_id,))
            row = cur.fetchone()
            cur.close()
            return row

    try:
        row = await run_in_threadpool(_get)
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error obteniendo documento: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener el documento")


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: int):
    def _delete():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM documents WHERE id = %s RETURNING id, filename;", (doc_id,))
            deleted = cur.fetchone()
            cur.close()
            return deleted

    try:
        deleted = await run_in_threadpool(_delete)
        if not deleted:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        logger.info(f"🗑️ Eliminado: {deleted['filename']}")
        return {"status": "success", "message": "Documento eliminado"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error eliminando documento: {e}")
        raise HTTPException(status_code=500, detail="Error al eliminar el documento")


@app.put("/api/documents/{doc_id}/rename")
async def rename_document(doc_id: int, request: RenameRequest):
    new_name = request.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")

    def _rename():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE documents SET filename = %s WHERE id = %s
                RETURNING id, filename;
            """, (new_name, doc_id))
            updated = cur.fetchone()
            cur.close()
            return updated

    try:
        updated = await run_in_threadpool(_rename)
        if not updated:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        return {
            "status": "success",
            "document": {"id": updated["id"], "title": updated["filename"]}
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error renombrando: {e}")
        raise HTTPException(status_code=500, detail="Error al renombrar el documento")


# ====================== MODO EJERCICIOS ======================

@app.post("/api/exercises/process")
async def process_exercise_document(document_id: int = Form(...)):
    logger.info(f"📝 Procesando ejercicios del documento {document_id}")

    def _get_doc():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, full_text, doc_type, pages FROM documents WHERE id = %s", (document_id,))
            doc = cur.fetchone()
            cur.close()
            return doc

    try:
        doc = await run_in_threadpool(_get_doc)

        if not doc:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        if doc["doc_type"] != "exercise":
            raise HTTPException(status_code=400, detail="El documento no es de tipo exercise")

        full_text = sanitize_text_for_prompt(truncate_text(doc["full_text"] or ""))

        if not full_text.strip():
            raise HTTPException(status_code=422, detail="no_exercises_found")

        system_prompt = """
Eres un experto en educación y extracción de ejercicios. Tu tarea es identificar y extraer TODOS los enunciados de ejercicios, problemas y preguntas que un alumno tenga que resolver.

QUÉ SÍ DEBES EXTRAER (considera estos como ejercicios válidos):
- Problemas numerados (1., 2., 3..., Ejercicio 1, Problema 1, etc.)
- Preguntas con letras (a), b), c)...) cuando son independientes
- Problemas de física, matemáticas, química, etc. aunque no estén numerados
- Enunciados que empiecen con "Calcula", "Determina", "Resuelve", "Demuestra", "Halla", "Encuentra", "Explica", "Indica", etc.
- Cualquier texto que plantee una tarea concreta al alumno

QUÉ DEBES IGNORAR:
- Títulos de temas o capítulos
- Texto teórico puro (definiciones, explicaciones largas sin pregunta)
- Instrucciones generales del examen ("Contesta las siguientes preguntas")
- Soluciones o respuestas ya dadas

REGLAS IMPORTANTES:
1. Extrae el enunciado COMPLETO de cada ejercicio (incluyendo datos, condiciones y la pregunta).
2. Si un ejercicio tiene varios apartados (a, b, c), puedes agruparlos como un solo ejercicio o separarlos si son independientes. Prefiere agruparlos si forman parte del mismo problema.
3. Numera los ejercicios de forma secuencial empezando por 1.
4. Limpia el texto: elimina numeraciones originales si es necesario, pero mantén el contenido intacto.
5. Si realmente no hay ningún ejercicio → responde exactamente: {"error": "no_exercises_found"}
6. Si el contenido no es educativo → responde exactamente: {"error": "invalid_content"}

FORMATO DE RESPUESTA (SOLO JSON, sin texto adicional):
{
  "exercises": [
    {
      "number": 1,
      "statement": "Enunciado completo del primer ejercicio aquí..."
    },
    {
      "number": 2,
      "statement": "Enunciado completo del segundo ejercicio aquí..."
    }
  ]
}
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Extrae todos los ejercicios del siguiente texto:\n\n{full_text}"}
        ]

        response_text = await call_grok(messages, temperature=0.15, timeout=180.0)

        try:
            data = extract_json_from_response(response_text)
        except Exception as parse_err:
            logger.warning(f"No se pudo parsear JSON de Grok: {parse_err}")
            logger.warning(f"Respuesta recibida (primeros 600 chars): {response_text[:600]}")
            raise HTTPException(status_code=422, detail="no_exercises_found")

        if "error" in data:
            raise HTTPException(status_code=422, detail=data["error"])

        exercises = data.get("exercises", [])
        if not exercises or not isinstance(exercises, list):
            raise HTTPException(status_code=422, detail="no_exercises_found")

        # Limpieza y validación de cada ejercicio
        clean_exercises = []
        for i, ex in enumerate(exercises):
            statement = (ex.get("statement") or "").strip()
            if len(statement) < 15:  # Muy corto, probablemente basura
                continue
            clean_exercises.append({
                "number": i + 1,
                "statement": statement
            })

        if not clean_exercises:
            raise HTTPException(status_code=422, detail="no_exercises_found")

        def _save_exercises():
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM exercises WHERE document_id = %s", (document_id,))
                for ex in clean_exercises:
                    cur.execute("""
                        INSERT INTO exercises (document_id, exercise_number, statement)
                        VALUES (%s, %s, %s)
                    """, (document_id, ex["number"], ex["statement"]))
                cur.close()

        await run_in_threadpool(_save_exercises)

        logger.info(f"✅ {len(clean_exercises)} ejercicios extraídos y guardados")
        return {
            "status": "success",
            "count": len(clean_exercises),
            "exercises": clean_exercises
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error procesando ejercicios (doc {document_id}): {type(e).__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Error al procesar los ejercicios")


@app.post("/api/exercises/generate")
async def generate_exercises(request: GenerateExercisesRequest):
    logger.info("🧠 Generando nuevos ejercicios")

    if not request.source_document_id and not request.reference_document_id:
        raise HTTPException(
            status_code=400,
            detail="Debes seleccionar al menos un texto de referencia o un archivo de ejercicios de referencia"
        )

    try:
        source_text = ""
        reference_text = ""

        def _load_texts():
            with get_db_connection() as conn:
                cur = conn.cursor()
                src = None
                ref = None
                if request.source_document_id:
                    cur.execute("SELECT full_text, doc_type, pages FROM documents WHERE id = %s", (request.source_document_id,))
                    src = cur.fetchone()
                if request.reference_document_id:
                    cur.execute("SELECT full_text, doc_type, pages FROM documents WHERE id = %s", (request.reference_document_id,))
                    ref = cur.fetchone()
                cur.close()
                return src, ref

        source, ref = await run_in_threadpool(_load_texts)

        if request.source_document_id:
            if not source or source["doc_type"] != "text":
                raise HTTPException(status_code=400, detail="El documento fuente debe ser de tipo text")
            source_text = sanitize_text_for_prompt(truncate_text(source["full_text"] or ""))

        if request.reference_document_id:
            if not ref or ref["doc_type"] != "exercise":
                raise HTTPException(status_code=400, detail="El documento de referencia debe ser de tipo exercise")
            if ref["pages"] and ref["pages"] > MAX_PAGES_EXERCISES:
                raise HTTPException(status_code=400, detail=f"Máximo {MAX_PAGES_EXERCISES} páginas en la referencia")
            reference_text = sanitize_text_for_prompt(truncate_text(ref["full_text"] or ""))

        system_prompt = """
Eres un profesor experto. Genera ejercicios educativos de calidad.

REGLAS ESTRICTAS:
1. Solo generas ejercicios reales y bien formulados.
2. Si el contenido no es adecuado → responde EXACTAMENTE:
   {"error": "invalid_content"}
3. Responde SOLO con este JSON:
{
  "exercises": [
    {"number": 1, "statement": "enunciado claro y completo"},
    {"number": 2, "statement": "enunciado claro y completo"}
  ]
}
Genera entre 4 y 8 ejercicios de dificultad progresiva.
No añadas ninguna explicación fuera del JSON.
"""

        user_content = ""
        if source_text:
            user_content += f"Texto base:\n\n{source_text}\n\n"
        if reference_text:
            user_content += f"Ejercicios de referencia (estilo a seguir):\n\n{reference_text}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        response_text = await call_grok(messages, temperature=0.4)

        try:
            data = extract_json_from_response(response_text)
        except Exception:
            raise HTTPException(status_code=422, detail="invalid_content")

        if "error" in data:
            raise HTTPException(status_code=422, detail=data["error"])

        exercises = data.get("exercises", [])
        if not exercises:
            raise HTTPException(status_code=422, detail="no_exercises_found")

        new_name = f"Ejercicios generados - {datetime.now().strftime('%d/%m/%Y %H:%M')}"

        def _save_generated():
            with get_db_connection() as conn:
                cur = conn.cursor()
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
                cur.close()
                return new_doc_id

        new_doc_id = await run_in_threadpool(_save_generated)

        logger.info(f"✅ {len(exercises)} ejercicios generados → documento {new_doc_id}")

        return {
            "status": "success",
            "document_id": new_doc_id,
            "count": len(exercises),
            "exercises": exercises
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error generando ejercicios: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al generar los ejercicios")


@app.get("/api/exercises/{document_id}")
async def list_exercises(document_id: int):
    def _list():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, exercise_number, statement
                FROM exercises
                WHERE document_id = %s
                ORDER BY exercise_number ASC;
            """, (document_id,))
            rows = cur.fetchall()
            cur.close()
            return rows

    try:
        rows = await run_in_threadpool(_list)
        return {
            "exercises": [{
                "id": row["id"],
                "number": row["exercise_number"],
                "statement": row["statement"]
            } for row in rows]
        }
    except Exception as e:
        logger.error(f"Error listando ejercicios: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener los ejercicios")


@app.get("/api/exercises/{document_id}/{exercise_number}")
async def get_exercise(document_id: int, exercise_number: int):
    def _get():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, exercise_number, statement
                FROM exercises
                WHERE document_id = %s AND exercise_number = %s;
            """, (document_id, exercise_number))
            row = cur.fetchone()
            cur.close()
            return row

    try:
        row = await run_in_threadpool(_get)
        if not row:
            raise HTTPException(status_code=404, detail="Ejercicio no encontrado")
        return {
            "id": row["id"],
            "number": row["exercise_number"],
            "statement": row["statement"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error obteniendo ejercicio: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener el ejercicio")


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
        logger.error(f"Error TTS: {e}")
        raise HTTPException(status_code=500, detail="Error al generar el audio")


# ====================== CHAT ======================
@app.post("/api/chat")
async def chat_with_grok(request: ChatRequest):
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="Error de configuración del servidor")

    try:
        mode = request.mode or "doubt"
        safe_text = sanitize_text_for_prompt(truncate_text(request.full_text))

        if mode == "step":
            system_content = (
                "Eres un profesor paciente, claro y didáctico. Estás resolviendo un ejercicio paso a paso.\n\n"
                "REGLAS OBLIGATORIAS:\n"
                "1. NUNCA empieces con 'Lee el enunciado' ni nada similar.\n"
                "2. Empieza siempre con una explicación intuitiva y sencilla, y después ve detallando la técnica.\n"
                "3. Da SOLO un paso cada vez.\n"
                "4. Después de cada paso pregunta exactamente:\n"
                "   \"¿Tienes alguna duda sobre este paso o quieres que continúe la explicación?\"\n"
                "5. Cuando hayas terminado toda la resolución di claramente:\n"
                "   \"He terminado la resolución del ejercicio.\"\n"
                "6. Usa texto limpio. No uses markdown de negrita (**texto**), ni guiones innecesarios.\n"
                "7. Sé natural y conversacional.\n\n"
                "=== EJERCICIO ===\n"
                f"{safe_text}\n"
                "=== FIN ==="
            )
        elif mode == "solution":
            system_content = (
                "Eres un profesor que corrige la solución de un alumno.\n"
                "Explica los errores y aciertos de forma clara y didáctica.\n"
                "Si recibes una imagen de la solución, analízala visualmente con cuidado.\n"
                "Después de cada observación pregunta si quiere continuar.\n"
                "Usa texto limpio, sin markdown de negrita ni guiones innecesarios.\n\n"
                "=== EJERCICIO ===\n"
                f"{safe_text}\n"
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
                f"{safe_text}\n"
                "=== FIN ==="
            )

        messages = [{"role": "system", "content": system_content}]

        for msg in request.history:
            messages.append({"role": msg.role, "content": msg.content})

        # Mensaje del usuario (posible multimodal)
        if mode == "solution" and request.image_base64 and request.image_mime:
            mime = request.image_mime.lower()
            if mime in ("image/jpeg", "image/jpg", "image/png"):
                data_url = f"data:{mime};base64,{request.image_base64}"
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                                "detail": "high"
                            }
                        },
                        {
                            "type": "text",
                            "text": request.question
                        }
                    ]
                })
            else:
                messages.append({"role": "user", "content": request.question})
        else:
            messages.append({"role": "user", "content": request.question})

        answer = await call_grok(messages, temperature=0.45)
        return {"answer": answer}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error en chat: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al procesar la consulta")
