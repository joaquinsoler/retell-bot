import os
import io
import json
import logging
import re
import base64
import traceback
from typing import List, Optional
from contextlib import contextmanager

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import edge_tts
import httpx
from PIL import Image

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

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
GROK_MODEL = "grok-4.3"   # ← más rápido (menor latencia)


# DPI fijo y consistente para visualización y recorte
RENDER_DPI = 150


# ====================== BASE DE DATOS ======================
@contextmanager
def get_db_connection():
    if not DATABASE_URL:
        raise Exception("No se encontró DATABASE_URL")
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

        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(doc_type);")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS selections (
                id SERIAL PRIMARY KEY,
                document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

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

        # Tabla antigua (compatibilidad)
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
        logger.info("✅ Base de datos lista")


@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:
        logger.error(f"❌ Error init DB: {e}")


# ====================== MODELOS ======================
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    question: str
    full_text: Optional[str] = ""
    history: Optional[List[ChatMessage]] = []
    mode: Optional[str] = "doubt"
    image_base64: Optional[str] = None
    image_mime: Optional[str] = None
    solution_image_base64: Optional[str] = None
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


# ====================== UTILIDADES ======================
async def call_grok(messages: list, temperature: float = 0.3, timeout: float = 90.0) -> str:
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="Falta GROK_API_KEY")

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
        logger.error(f"Error Grok {response.status_code}: {response.text[:600]}")
        raise HTTPException(status_code=500, detail="Error al contactar con Grok")

    data = response.json()
    return data["choices"][0]["message"]["content"]


def render_pdf_page_to_image(pdf_bytes: bytes, page_number: int, dpi: int = RENDER_DPI) -> Image.Image:
    """Renderiza una página del PDF a imagen PIL con el DPI fijo"""
    if fitz is None:
        raise HTTPException(status_code=500, detail="PyMuPDF (fitz) no está instalado")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page_number < 1 or page_number > len(doc):
            raise HTTPException(status_code=400, detail="Número de página inválido")
        page = doc.load_page(page_number - 1)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return img
    finally:
        doc.close()


def tight_crop(image: Image.Image, padding: int = 14) -> Image.Image:
    """Elimina espacios en blanco excesivos alrededor del contenido"""
    gray = image.convert("L")
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


def image_to_jpeg_bytes(img: Image.Image, quality: int = 86) -> bytes:
    buffer = io.BytesIO()
    img.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def optimize_image_for_grok(image_base64: str, max_width: int = 1024, quality: int = 70) -> str:
    """
    Reduce el tamaño de la imagen para bajar latencia y coste de visión.
    """
    try:
        img_data = base64.b64decode(image_base64)
        img = Image.open(io.BytesIO(img_data)).convert("RGB")

        # Redimensionar si es demasiado grande
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.LANCZOS)

        # Comprimir
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception as e:
        logger.warning(f"No se pudo optimizar la imagen: {e}")
        return image_base64  # Si falla, devolvemos la original


# ====================== HEALTH ======================
@app.get("/health")
async def health():
    return {"status": "ok", "service": "lucsi", "render_dpi": RENDER_DPI, "model": GROK_MODEL}


# ====================== DOCUMENTOS ======================
@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    name: str = Form(...),
    doc_type: str = Form("text"),
    start_page: int = Form(1),
    end_page: Optional[int] = Form(None)
):
    logger.info(f"🚀 SUBIDA → tipo={doc_type} | nombre={name}")

    if doc_type not in ("text", "exercise"):
        raise HTTPException(status_code=400, detail="Tipo de documento no válido")

    final_name = (name or "").strip()
    if not final_name:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")

    try:
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="El archivo está vacío")

        size_bytes = len(contents)

        if fitz is None:
            raise HTTPException(status_code=500, detail="PyMuPDF no disponible")

        doc = fitz.open(stream=contents, filetype="pdf")
        total_pages = len(doc)
        doc.close()

        if total_pages == 0:
            raise HTTPException(status_code=400, detail="El PDF no tiene páginas")

        start_page = max(1, start_page)
        if end_page is None:
            end_page = total_pages
        end_page = min(total_pages, end_page)

        if start_page > end_page:
            raise HTTPException(status_code=400, detail="La página de inicio no puede ser mayor que la final")

        # Sin límite de páginas

        full_text = ""
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(contents))
            parts = []
            for i in range(start_page - 1, end_page):
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
                """, (
                    final_name, contents, full_text, size_bytes,
                    total_pages, doc_type, start_page, end_page
                ))
                return cur.fetchone()

        saved = await run_in_threadpool(_save)
        logger.info(f"✅ Guardado: {saved['filename']} (ID {saved['id']})")

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
        logger.error(f"❌ Error en upload: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Error al procesar el documento")


@app.get("/api/documents")
async def list_documents(doc_type: Optional[str] = None):
    def _list():
        with get_db_connection() as conn:
            cur = conn.cursor()
            sql = """
                SELECT id, filename, size_bytes, pages, doc_type, uploaded_at, full_text
                FROM documents 
            """
            if doc_type:
                cur.execute(sql + " WHERE doc_type = %s ORDER BY uploaded_at DESC;", (doc_type,))
            else:
                cur.execute(sql + " ORDER BY uploaded_at DESC;")
            return cur.fetchall()

    try:
        rows = await run_in_threadpool(_list)
        return {
            "documents": [{
                "id": r["id"],
                "title": r["filename"],
                "date": r["uploaded_at"].strftime("%d/%m/%Y") if r["uploaded_at"] else "",
                "pages": r["pages"],
                "doc_type": r["doc_type"],
                "full_text": r["full_text"]
            } for r in rows]
        }
    except Exception as e:
        logger.error(f"Error listando documentos: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener documentos")


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: int):
    def _get():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, filename, pages, doc_type, uploaded_at, full_text
                FROM documents WHERE id = %s;
            """, (doc_id,))
            return cur.fetchone()

    row = await run_in_threadpool(_get)
    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {
        "id": row["id"],
        "title": row["filename"],
        "pages": row["pages"],
        "doc_type": row["doc_type"],
        "uploaded_at": str(row["uploaded_at"]),
        "full_text": row["full_text"]
    }


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: int):
    def _delete():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM documents WHERE id = %s RETURNING id;", (doc_id,))
            return cur.fetchone()

    deleted = await run_in_threadpool(_delete)
    if not deleted:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {"status": "success"}


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
            return cur.fetchone()

    updated = await run_in_threadpool(_rename)
    if not updated:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {"status": "success", "document": {"id": updated["id"], "title": updated["filename"]}}


# ====================== RENDER PÁGINA → IMAGEN ======================
@app.get("/api/documents/{doc_id}/page/{page_number}")
async def get_pdf_page_image(doc_id: int, page_number: int):
    """
    Convierte una página del PDF en imagen.
    Usa siempre RENDER_DPI para que las coordenadas coincidan con el recorte.
    """
    def _get_pdf():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT content, pages FROM documents WHERE id = %s;", (doc_id,))
            return cur.fetchone()

    row = await run_in_threadpool(_get_pdf)
    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    if page_number < 1 or page_number > (row["pages"] or 1):
        raise HTTPException(status_code=400, detail="Página fuera de rango")

    try:
        img = render_pdf_page_to_image(row["content"], page_number, dpi=RENDER_DPI)
        img_bytes = image_to_jpeg_bytes(img, quality=82)
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        return {
            "page": page_number,
            "width": img.width,
            "height": img.height,
            "image_base64": b64,
            "mime": "image/jpeg",
            "dpi": RENDER_DPI
        }
    except HTTPException:
        raise
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
                       (SELECT COUNT(*) FROM selection_exercises se WHERE se.selection_id = s.id) AS count
                FROM selections s
                WHERE s.document_id = %s
                ORDER BY s.created_at DESC;
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
    Recibe las coordenadas (en el mismo DPI que se mostró la imagen)
    y genera los recortes finales con tight_crop.
    """
    if not request.crops:
        raise HTTPException(status_code=400, detail="No hay ejercicios seleccionados")

    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre de la selección no puede estar vacío")

    def _get_pdf():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT content, pages FROM documents WHERE id = %s;", (request.document_id,))
            return cur.fetchone()

    row = await run_in_threadpool(_get_pdf)
    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    pdf_bytes = row["content"]
    processed = []

    for i, crop in enumerate(request.crops):
        try:
            # Importante: mismo DPI que el frontend
            page_img = render_pdf_page_to_image(pdf_bytes, crop.page, dpi=RENDER_DPI)
            img_w, img_h = page_img.size

            x1 = max(0, int(round(crop.x)))
            y1 = max(0, int(round(crop.y)))
            x2 = min(img_w, int(round(crop.x + crop.width)))
            y2 = min(img_h, int(round(crop.y + crop.height)))

            if x2 - x1 < 10 or y2 - y1 < 10:
                continue

            cropped = page_img.crop((x1, y1, x2, y2))
            cropped = tight_crop(cropped, padding=14)
            img_bytes = image_to_jpeg_bytes(cropped, quality=88)

            processed.append({
                "order_num": i + 1,
                "title": crop.title or f"Ejercicio {i + 1}",
                "image": img_bytes,
                "page_number": crop.page
            })
        except Exception as e:
            logger.warning(f"Error procesando crop {i}: {e}")
            continue

    if not processed:
        raise HTTPException(status_code=400, detail="No se pudo procesar ningún ejercicio")

    def _save():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO selections (document_id, name)
                VALUES (%s, %s) RETURNING id;
            """, (request.document_id, name))
            selection_id = cur.fetchone()["id"]

            for ex in processed:
                cur.execute("""
                    INSERT INTO selection_exercises
                        (selection_id, order_num, title, image, page_number)
                    VALUES (%s, %s, %s, %s, %s);
                """, (selection_id, ex["order_num"], ex["title"], ex["image"], ex["page_number"]))
            return selection_id

    selection_id = await run_in_threadpool(_save)
    logger.info(f"✅ Selección {selection_id} creada con {len(processed)} ejercicios")

    return {
        "status": "success",
        "selection_id": selection_id,
        "count": len(processed)
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
                ORDER BY order_num ASC;
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
    new_name = request.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")

    def _rename():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE selections SET name = %s WHERE id = %s RETURNING id;", (new_name, selection_id))
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
            cur.execute("DELETE FROM selections WHERE id = %s RETURNING id;", (selection_id,))
            return cur.fetchone()

    deleted = await run_in_threadpool(_delete)
    if not deleted:
        raise HTTPException(status_code=404, detail="Selección no encontrada")
    return {"status": "success"}


# ====================== CHAT (optimizado para baja latencia) ======================
@app.post("/api/chat")
async def chat_with_grok(request: ChatRequest):
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="Falta GROK_API_KEY")

    try:
        mode = request.mode or "doubt"

        if mode == "step":
            system_content = (
                "Eres un profesor paciente y didáctico. Estás resolviendo un ejercicio paso a paso.\n"
                "REGLAS:\n"
                "1. Nunca empieces con 'Lee el enunciado'.\n"
                "2. Empieza con una idea intuitiva clara.\n"
                "3. Da solo un paso cada vez.\n"
                "4. Después de cada paso pregunta si tiene dudas o quiere continuar la explicación.\n"
                "5. Cuando termines di claramente: 'He terminado la resolución del ejercicio.'\n"
                "6. Usa texto limpio, sin markdown innecesario (**negrita**, guiones excesivos, etc.).\n"
            )
        elif mode == "solution":
            system_content = (
                "Eres un profesor que corrige la solución de un alumno.\n"
                "Analiza la imagen de la solución si la recibes.\n"
                "Explica errores y aciertos de forma clara y constructiva.\n"
                "Después de cada observación pregunta si quiere continuar.\n"
            )
        else:
            system_content = (
                "Eres un profesor experto, claro y paciente.\n"
                "El alumno tiene una duda sobre el ejercicio.\n"
                "Puedes explicar conceptos, métodos o cualquier duda relacionada con el contenido.\n"
                "Empieza con ideas intuitivas y después profundiza si es necesario.\n"
            )

        messages = [{"role": "system", "content": system_content}]

        for msg in (request.history or []):
            messages.append({"role": msg.role, "content": msg.content})

        user_content = []

        # Optimizamos la imagen del enunciado
        if request.image_base64 and request.image_mime:
            optimized = optimize_image_for_grok(request.image_base64)
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{optimized}",
                    "detail": "low"          # ← antes era "high"
                }
            })

        # Optimizamos la imagen de la solución del alumno
        if mode == "solution" and request.solution_image_base64:
            optimized_sol = optimize_image_for_grok(request.solution_image_base64)
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{optimized_sol}",
                    "detail": "low"
                }
            })

        user_content.append({"type": "text", "text": request.question})
        messages.append({"role": "user", "content": user_content})

        answer = await call_grok(messages, temperature=0.4, timeout=90.0)
        return {"answer": answer}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error en chat: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Error al procesar la consulta")


# ====================== TTS ======================
@app.post("/api/tts")
async def text_to_speech(text: str = Form(...), voice: str = Form("es-ES-AlvaroNeural")):
    if not text.strip():
        raise HTTPException(status_code=400, detail="El texto no puede estar vacío")
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
        raise HTTPException(status_code=500, detail="Error al generar el audio")
