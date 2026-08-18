import os
import io
import json
import logging
from datetime import datetime

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL,
            content BYTEA NOT NULL,
            full_text TEXT,
            size_bytes INTEGER,
            pages INTEGER,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        DO $$ 
        BEGIN 
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='documents' AND column_name='full_text'
            ) THEN
                ALTER TABLE documents ADD COLUMN full_text TEXT;
            END IF;
        END $$;
    """)

    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ Tabla 'documents' lista")


@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:
        logger.error(f"❌ Error al inicializar DB: {e}")


# ====================== MODELOS ======================
class ChatMessage(BaseModel):
    role: str          # "user" o "assistant"
    content: str

class ChatRequest(BaseModel):
    question: str
    full_text: str
    history: Optional[List[ChatMessage]] = []


# ====================== ENDPOINTS DOCUMENTOS ======================

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...), name: str = Form(...)):
    logger.info("=" * 70)
    logger.info("🚀 INICIO DE SUBIDA DE DOCUMENTO")

    try:
        if file.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Solo se permiten archivos PDF")

        final_name = name.strip()
        if not final_name:
            raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")

        contents = await file.read()
        size_bytes = len(contents)

        pdf_file = io.BytesIO(contents)
        reader = PdfReader(pdf_file)
        num_pages = len(reader.pages)

        full_text_parts = []
        for page in reader.pages:
            full_text_parts.append(page.extract_text() or "")

        full_text = "\n\n".join(full_text_parts).strip()

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO documents (filename, content, full_text, size_bytes, pages)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, filename, size_bytes, pages, uploaded_at;
        """, (final_name, contents, full_text, size_bytes, num_pages))

        saved = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"✅ Documento guardado: {saved['filename']} (ID {saved['id']})")
        logger.info("=" * 70)

        return {
            "status": "success",
            "document": {
                "id": saved["id"],
                "filename": saved["filename"],
                "size_bytes": saved["size_bytes"],
                "pages": saved["pages"],
                "uploaded_at": str(saved["uploaded_at"])
            }
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents")
async def list_documents():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, filename, size_bytes, pages, uploaded_at
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
            "size_bytes": row["size_bytes"]
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
            SELECT id, filename, full_text, size_bytes, pages, uploaded_at
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

        return {"status": "success", "message": "Documento eliminado"}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== TEXT-TO-SPEECH ======================

@app.post("/api/tts")
async def text_to_speech(
    text: str = Form(...),
    voice: str = Form("es-ES-AlvaroNeural")
):
    logger.info(f"🔊 TTS: {text[:60]}...")

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
        logger.error(f"❌ Error TTS: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ====================== CHAT CON GROK ======================

@app.post("/api/chat")
async def chat_with_grok(request: ChatRequest):
    """
    Envía la pregunta + texto completo del documento + historial completo a Grok.
    """
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="GROK_API_KEY no configurada")

    logger.info("🤖 Nueva consulta a Grok")
    logger.info(f"   Pregunta: {request.question[:80]}...")

    try:
        # Construir los mensajes
        messages = [
            {
                "role": "system",
                "content": (
                    "Eres un profesor experto, claro y paciente. "
                    "El estudiante está leyendo el siguiente documento y tiene dudas. "
                    "Responde de forma clara, concisa y didáctica, basándote siempre en el contenido del documento.\n\n"
                    "=== DOCUMENTO COMPLETO ===\n"
                    f"{request.full_text}\n"
                    "=== FIN DEL DOCUMENTO ==="
                )
            }
        ]

        # Añadir todo el historial de la conversación
        for msg in request.history:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })

        # Añadir la nueva pregunta
        messages.append({
            "role": "user",
            "content": request.question
        })

        # Llamada a la API de Grok
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                GROK_API_URL,
                headers={
                    "Authorization": f"Bearer {GROK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": GROK_MODEL,
                    "messages": messages,
                    "temperature": 0.5
                }
            )

        if response.status_code != 200:
            logger.error(f"Error Grok API: {response.status_code} - {response.text}")
            raise HTTPException(status_code=500, detail="Error al contactar con Grok")

        data = response.json()
        answer = data["choices"][0]["message"]["content"]

        logger.info("✅ Respuesta de Grok recibida")

        return {
            "answer": answer
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Error en chat: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
