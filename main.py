import os
import io
import json
import logging
import re
import base64
import traceback
from datetime import datetime
from typing import List, Optional
from contextlib import contextmanager

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from pypdf import PdfReader
import psycopg2
from psycopg2.extras import RealDictCursor
import edge_tts
import httpx

# Para renderizar y recortar PDF
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from PIL import Image

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
MAX_PAGES_EXERCISES = 15
MAX_TEXT_CHARS = 120000


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

        # Tabla documents (ya existía)
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

        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(doc_type);")

        # Tabla de selecciones
        cur.execute("""
            CREATE TABLE IF NOT EXISTS selections (
                id SERIAL PRIMARY KEY,
                document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Ejercicios dentro de una selección (ahora son imágenes)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS selection_exercises (
                id SERIAL PRIMARY KEY,
                selection_id INTEGER REFERENCES selections(id) ON DELETE CASCADE,
                order_num INTEGER NOT NULL,
                title TEXT,
                image BYTEA NOT NULL,
                page_number INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_selections_document ON selections(document_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_selection_exercises_selection ON selection_exercises(selection_id);")

        # Tabla antigua de exercises (la mantenemos por compatibilidad con generación)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS exercises (
                id SERIAL PRIMARY KEY,
                document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                exercise_number INTEGER NOT NULL,
                statement TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
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
    full_text: Optional[str] = ""
    history: Optional[List[ChatMessage]] = []
    mode: Optional[str] = "doubt"
    image_base64: Optional[str] = None          # imagen del enunciado
    image_mime: Optional[str] = None
    solution_image_base64: Optional[str] = None # foto de la solución del alumno
    solution_image_mime: Optional[str] = None

class RenameRequest(BaseModel):
    name: str

class CropItem(BaseModel):
    page: int
    x: float
    y: float
    width: float
    height: float
    title: Optional[str] = None

class CreateSelectionRequest(BaseModel):
    document_id: int
    name: str
    crops: List[CropItem]

class GenerateExercisesRequest(BaseModel):
    source_document_id: Optional[int] = None
    reference_document_id: Optional[int] = None


# ====================== UTILIDADES ======================
def sanitize_text_for_prompt(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"={3,}.*?={3,}", "[contenido eliminado]", text, flags=re.DOTALL)
    return text.strip()

def truncate_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars] + "\n\n[... texto truncado ...]"

def extract_json_from_response(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    cleaned = re.sub(r'```json|```', '', text).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except Exception:
            pass
    raise ValueError("No se pudo extraer JSON")

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


def render_pdf_page_to_image(pdf_bytes: bytes, page_number: int, dpi: int = 150) -> Image.Image:
    """Renderiza una página del PDF a imagen PIL"""
    if fitz is None:
        raise HTTPException(status_code=500, detail="PyMuPDF no está instalado")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if page_number < 1 or page_number > len(doc):
        doc.close()
        raise HTTPException(status_code=400, detail="Número de página inválido")

    page = doc.load_page(page_number - 1)
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def tight_crop(image: Image.Image, padding: int = 12) -> Image.Image:
    """
    Ajuste clásico del recorte:
    elimina el máximo de espacio en blanco alrededor del contenido.
    """
    # Convertir a escala de grises para detectar contenido
    gray = image.convert("L")
    # Umbral: todo lo que no sea casi blanco se considera contenido
    bw = gray.point(lambda x: 0 if x < 245 else 255, "1")
    bbox = bw.getbbox()
    if not bbox:
        return image

    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)

    return image.crop((left, top, right, bottom))


def image_to_bytes(img: Image.Image, fmt: str = "JPEG", quality: int = 85) -> bytes:
    buffer = io.BytesIO()
    if fmt.upper() == "JPEG":
        img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
    else:
        img.save(buffer, format=fmt)
    return buffer.getvalue()


# ====================== HEALTH ======================
@app.get("/health")
async def health():
    return {"status": "ok", "service": "lucsi"}


# ====================== DOCUMENTOS ======================
@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    name: str = Form(...),
    doc_type: str = Form("text"),
    start_page: int = Form(1),
    end_page: Optional[int] = Form(None)
):
    logger.info(f"🚀 SUBIDA → tipo={doc_type}")

    if doc_type not in ("text", "exercise"):
        raise HTTPException(status_code=400, detail="Tipo de documento no válido")

    try:
        contents = await file.read()
        size_bytes = len(contents)

        if fitz:
            doc = fitz.open(stream=contents, filetype="pdf")
            total_pages = len(doc)
            doc.close()
        else:
            reader = PdfReader(io.BytesIO(contents))
            total_pages = len(reader.pages)

        if end_page is None:
            end_page = total_pages

        # Para el modo ejercicio limitamos páginas
        if doc_type == "exercise" and (end_page - start_page + 1) > MAX_PAGES_EXERCISES:
            raise HTTPException(status_code=400, detail=f"Máximo {MAX_PAGES_EXERCISES} páginas")

        # Extraemos texto solo para compatibilidad
        full_text = ""
        try:
            from pypdf import PdfReader as PR
            reader = PR(io.BytesIO(contents))
            parts = []
            for i in range(max(0, start_page-1), min(total_pages, end_page)):
                parts.append(reader.pages[i].extract_text() or "")
            full_text = "\n\n".join(parts)
        except Exception:
            pass

        def _save():
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO documents
                        (filename, content, full_text, size_bytes, pages, doc_type, start_page, end_page)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, filename, size_bytes, pages, doc_type, uploaded_at;
                """, (name.strip(), contents, full_text, size_bytes, total_pages, doc_type, start_page, end_page))
                return cur.fetchone()

        saved = await run_in_threadpool(_save)
        logger.info(f"✅ Documento guardado ID {saved['id']}")

        return {
            "status": "success",
            "document": {
                "id": saved["id"],
                "filename": saved["filename"],
                "pages": saved["pages"],
                "doc_type": saved["doc_type"],
                "uploaded_at": str(saved["uploaded_at"])
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error upload: {e}")
        raise HTTPException(status_code=500, detail="Error al subir el documento")


@app.get("/api/documents")
async def list_documents(doc_type: Optional[str] = None):
    def _list():
        with get_db_connection() as conn:
            cur = conn.cursor()
            if doc_type:
                cur.execute("""
                    SELECT id, filename, size_bytes, pages, doc_type, uploaded_at
                    FROM documents WHERE doc_type = %s ORDER BY uploaded_at DESC;
                """, (doc_type,))
            else:
                cur.execute("""
                    SELECT id, filename, size_bytes, pages, doc_type, uploaded_at
                    FROM documents ORDER BY uploaded_at DESC;
                """)
            return cur.fetchall()

    rows = await run_in_threadpool(_list)
    return {
        "documents": [{
            "id": r["id"],
            "title": r["filename"],
            "date": r["uploaded_at"].strftime("%d/%m/%Y") if r["uploaded_at"] else "",
            "pages": r["pages"],
            "doc_type": r["doc_type"]
        } for r in rows]
    }


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: int):
    def _get():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, filename, pages, doc_type, uploaded_at FROM documents WHERE id = %s", (doc_id,))
            return cur.fetchone()

    row = await run_in_threadpool(_get)
    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {
        "id": row["id"],
        "title": row["filename"],
        "pages": row["pages"],
        "doc_type": row["doc_type"],
        "uploaded_at": str(row["uploaded_at"])
    }


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: int):
    def _delete():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM documents WHERE id = %s RETURNING id", (doc_id,))
            return cur.fetchone()

    deleted = await run_in_threadpool(_delete)
    if not deleted:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {"status": "success"}


@app.put("/api/documents/{doc_id}/rename")
async def rename_document(doc_id: int, request: RenameRequest):
    def _rename():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET filename = %s WHERE id = %s RETURNING id, filename",
                        (request.name.strip(), doc_id))
            return cur.fetchone()

    updated = await run_in_threadpool(_rename)
    if not updated:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {"status": "success", "document": {"id": updated["id"], "title": updated["filename"]}}


# ====================== RENDERIZAR PÁGINA DEL PDF ======================
@app.get("/api/documents/{doc_id}/page/{page_number}")
async def get_pdf_page_image(doc_id: int, page_number: int, dpi: int = 140):
    """Devuelve una página del PDF como imagen JPEG (base64)"""
    def _get_pdf():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT content, pages FROM documents WHERE id = %s", (doc_id,))
            return cur.fetchone()

    row = await run_in_threadpool(_get_pdf)
    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    if page_number < 1 or page_number > row["pages"]:
        raise HTTPException(status_code=400, detail="Página fuera de rango")

    try:
        img = render_pdf_page_to_image(row["content"], page_number, dpi=dpi)
        img_bytes = image_to_bytes(img, "JPEG", quality=82)
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        return {
            "page": page_number,
            "width": img.width,
            "height": img.height,
            "image_base64": b64,
            "mime": "image/jpeg"
        }
    except Exception as e:
        logger.error(f"Error renderizando página: {e}")
        raise HTTPException(status_code=500, detail="Error al renderizar la página")


# ====================== SELECCIONES ======================
@app.get("/api/documents/{doc_id}/selections")
async def list_selections(doc_id: int):
    def _list():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT s.id, s.name, s.created_at,
                       (SELECT COUNT(*) FROM selection_exercises se WHERE se.selection_id = s.id) as count
                FROM selections s
                WHERE s.document_id = %s
                ORDER BY s.created_at DESC
            """, (doc_id,))
            return cur.fetchall()

    rows = await run_in_threadpool(_list)
    return {
        "selections": [{
            "id": r["id"],
            "name": r["name"],
            "count": r["count"],
            "date": r["created_at"].strftime("%d/%m/%Y %H:%M") if r["created_at"] else ""
        } for r in rows]
    }


@app.post("/api/selections")
async def create_selection(request: CreateSelectionRequest):
    """
    Crea una selección a partir de los recuadros que dibujó el alumno.
    Aplica el ajuste clásico de recorte (tight_crop).
    """
    if not request.crops:
        raise HTTPException(status_code=400, detail="No hay ejercicios seleccionados")

    def _get_pdf():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT content, pages FROM documents WHERE id = %s", (request.document_id,))
            return cur.fetchone()

    row = await run_in_threadpool(_get_pdf)
    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    pdf_bytes = row["content"]
    processed_exercises = []

    for i, crop in enumerate(request.crops):
        try:
            # Renderizar la página
            page_img = render_pdf_page_to_image(pdf_bytes, crop.page, dpi=160)

            # Convertir coordenadas relativas (0-1) o absolutas a píxeles
            # El frontend enviará coordenadas en píxeles de la imagen mostrada
            img_w, img_h = page_img.size
            x1 = max(0, int(crop.x))
            y1 = max(0, int(crop.y))
            x2 = min(img_w, int(crop.x + crop.width))
            y2 = min(img_h, int(crop.y + crop.height))

            if x2 <= x1 or y2 <= y1:
                continue

            cropped = page_img.crop((x1, y1, x2, y2))

            # === AJUSTE CLÁSICO DEL RECUADRO ===
            cropped = tight_crop(cropped, padding=15)

            img_bytes = image_to_bytes(cropped, "JPEG", quality=88)
            title = crop.title or f"Ejercicio {i+1}"

            processed_exercises.append({
                "order_num": i + 1,
                "title": title,
                "image": img_bytes,
                "page_number": crop.page
            })
        except Exception as e:
            logger.warning(f"Error procesando crop {i}: {e}")
            continue

    if not processed_exercises:
        raise HTTPException(status_code=400, detail="No se pudo procesar ningún ejercicio")

    def _save():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO selections (document_id, name)
                VALUES (%s, %s) RETURNING id
            """, (request.document_id, request.name.strip()))
            selection_id = cur.fetchone()["id"]

            for ex in processed_exercises:
                cur.execute("""
                    INSERT INTO selection_exercises
                        (selection_id, order_num, title, image, page_number)
                    VALUES (%s, %s, %s, %s, %s)
                """, (selection_id, ex["order_num"], ex["title"], ex["image"], ex["page_number"]))
            return selection_id

    selection_id = await run_in_threadpool(_save)
    logger.info(f"✅ Selección {selection_id} creada con {len(processed_exercises)} ejercicios")

    return {
        "status": "success",
        "selection_id": selection_id,
        "count": len(processed_exercises)
    }


@app.get("/api/selections/{selection_id}/exercises")
async def get_selection_exercises(selection_id: int):
    def _get():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, order_num, title, image, page_number
                FROM selection_exercises
                WHERE selection_id = %s
                ORDER BY order_num ASC
            """, (selection_id,))
            return cur.fetchall()

    rows = await run_in_threadpool(_get)
    if not rows:
        raise HTTPException(status_code=404, detail="Selección no encontrada o vacía")

    exercises = []
    for r in rows:
        b64 = base64.b64encode(r["image"]).decode("utf-8")
        exercises.append({
            "id": r["id"],
            "number": r["order_num"],
            "title": r["title"] or f"Ejercicio {r['order_num']}",
            "image_base64": b64,
            "mime": "image/jpeg",
            "page": r["page_number"]
        })

    return {"exercises": exercises}


@app.put("/api/selections/{selection_id}/rename")
async def rename_selection(selection_id: int, request: RenameRequest):
    def _rename():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE selections SET name = %s WHERE id = %s RETURNING id",
                        (request.name.strip(), selection_id))
            return cur.fetchone()

    updated = await run_in_threadpool(_rename)
    if not updated:
        raise HTTPException(status_code=404, detail="Selección no encontrada")
    return {"status": "success"}


@app.delete("/api/selections/{selection_id}")
async def delete_selection(selection_id: int):
    def _delete():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM selections WHERE id = %s RETURNING id", (selection_id,))
            return cur.fetchone()

    deleted = await run_in_threadpool(_delete)
    if not deleted:
        raise HTTPException(status_code=404, detail="Selección no encontrada")
    return {"status": "success"}


# ====================== CHAT (con soporte de imagen de enunciado) ======================
@app.post("/api/chat")
async def chat_with_grok(request: ChatRequest):
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="Error de configuración del servidor")

    try:
        mode = request.mode or "doubt"

        system_content = ""
        if mode == "step":
            system_content = (
                "Eres un profesor paciente y didáctico. Estás resolviendo un ejercicio paso a paso.\n"
                "REGLAS:\n"
                "1. Nunca empieces con 'Lee el enunciado'.\n"
                "2. Empieza con una idea intuitiva clara.\n"
                "3. Da solo un paso cada vez.\n"
                "4. Después de cada paso pregunta si tiene dudas o quiere continuar.\n"
                "5. Cuando termines di: 'He terminado la resolución del ejercicio.'\n"
                "6. Usa texto limpio, sin markdown innecesario.\n"
            )
        elif mode == "solution":
            system_content = (
                "Eres un profesor que corrige la solución de un alumno.\n"
                "Analiza la imagen de la solución si la recibes.\n"
                "Explica errores y aciertos de forma clara.\n"
                "Después de cada observación pregunta si quiere continuar.\n"
            )
        else:
            system_content = (
                "Eres un profesor experto, claro y paciente.\n"
                "El alumno tiene una duda sobre el ejercicio.\n"
                "Puedes explicar conceptos, métodos o cualquier duda relacionada.\n"
                "Empieza con ideas intuitivas.\n"
            )

        messages = [{"role": "system", "content": system_content}]

        for msg in (request.history or []):
            messages.append({"role": msg.role, "content": msg.content})

        # Construir el mensaje del usuario (posible multimodal)
        user_content = []

        # Imagen del enunciado (ejercicio seleccionado)
        if request.image_base64 and request.image_mime:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{request.image_mime};base64,{request.image_base64}",
                    "detail": "high"
                }
            })

        # Imagen de la solución del alumno (modo solution)
        if mode == "solution" and request.solution_image_base64 and request.solution_image_mime:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{request.solution_image_mime};base64,{request.solution_image_base64}",
                    "detail": "high"
                }
            })

        user_content.append({
            "type": "text",
            "text": request.question
        })

        messages.append({"role": "user", "content": user_content})

        answer = await call_grok(messages, temperature=0.45)
        return {"answer": answer}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error en chat: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Error al procesar la consulta")


# ====================== TTS ======================
@app.post("/api/tts")
async def text_to_speech(text: str = Form(...), voice: str = Form("es-ES-AlvaroNeural")):
    if not text.strip():
        raise HTTPException(status_code=400, detail="Texto vacío")
    try:
        communicate = edge_tts.Communicate(text=text.strip(), voice=voice)
        audio_buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.write(chunk["data"])
        audio_buffer.seek(0)
        return StreamingResponse(audio_buffer, media_type="audio/mpeg")
    except Exception as e:
        logger.error(f"Error TTS: {e}")
        raise HTTPException(status_code=500, detail="Error al generar audio")
