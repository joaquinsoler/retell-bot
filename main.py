import os
import io
import logging
import tempfile
from datetime import datetime

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pypdf import PdfReader
import psycopg2
from psycopg2.extras import RealDictCursor
import edge_tts

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

# ====================== BASE DE DATOS ======================
DATABASE_URL = os.getenv("DATABASE_URL")

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

    # Asegurar columna full_text
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


# ====================== ENDPOINTS DE DOCUMENTOS ======================

@app.post("/api/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    name: str = Form(...)
):
    logger.info("=" * 70)
    logger.info("🚀 INICIO DE SUBIDA DE DOCUMENTO")

    try:
        if file.content_type != "application/pdf":
            logger.error("❌ El archivo no es un PDF")
            raise HTTPException(status_code=400, detail="Solo se permiten archivos PDF")

        final_name = name.strip()
        if not final_name:
            raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")

        logger.info(f"📥 Archivo recibido: {file.filename}")
        logger.info(f"📝 Nombre asignado: {final_name}")

        contents = await file.read()
        size_bytes = len(contents)
        logger.info(f"📦 Tamaño: {size_bytes} bytes")

        # Extraer todo el texto
        pdf_file = io.BytesIO(contents)
        reader = PdfReader(pdf_file)
        num_pages = len(reader.pages)

        full_text_parts = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            full_text_parts.append(page_text)

        full_text = "\n\n".join(full_text_parts).strip()
        logger.info(f"📄 Texto completo extraído ({len(full_text)} caracteres) - {num_pages} páginas")

        # Guardar en base de datos
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO documents (filename, content, full_text, size_bytes, pages)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, filename, size_bytes, pages, uploaded_at;
        """, (final_name, contents, full_text, size_bytes, num_pages))

        saved = cur.fetchone()
        conn.commit()

        logger.info("✅ Documento guardado correctamente")
        logger.info(f"   → ID: {saved['id']}")
        logger.info(f"   → Nombre: {saved['filename']}")

        cur.close()
        conn.close()

        logger.info("🎉 PROCESO COMPLETADO CON ÉXITO")
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

        documents = []
        for row in rows:
            documents.append({
                "id": row["id"],
                "title": row["filename"],
                "date": row["uploaded_at"].strftime("%d %b %Y") if row["uploaded_at"] else "",
                "pages": row["pages"],
                "size_bytes": row["size_bytes"]
            })

        return {"documents": documents}

    except Exception as e:
        logger.error(f"Error listando documentos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: int):
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, filename, full_text, size_bytes, pages, uploaded_at
            FROM documents
            WHERE id = %s;
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
        logger.error(f"Error obteniendo documento: {e}")
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

        logger.info(f"🗑️ Documento eliminado: {deleted['filename']} (ID {doc_id})")
        return {"status": "success", "message": "Documento eliminado"}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error eliminando documento: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ====================== NUEVO ENDPOINT: TEXT-TO-SPEECH ======================

@app.post("/api/tts")
async def text_to_speech(
    text: str = Form(...),
    voice: str = Form("es-ES-AlvaroNeural")
):
    """
    Recibe texto y devuelve el audio en MP3 usando Microsoft Edge TTS.
    """
    logger.info("🔊 Petición TTS recibida")
    logger.info(f"   → Voz: {voice}")
    logger.info(f"   → Texto ({len(text)} caracteres): {text[:80]}...")

    if not text.strip():
        raise HTTPException(status_code=400, detail="El texto no puede estar vacío")

    try:
        # Generar audio con edge-tts
        communicate = edge_tts.Communicate(text=text.strip(), voice=voice)
        
        # Guardar temporalmente en memoria
        audio_buffer = io.BytesIO()
        
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.write(chunk["data"])

        audio_buffer.seek(0)
        
        logger.info("✅ Audio generado correctamente")

        return StreamingResponse(
            audio_buffer,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline; filename=speech.mp3"
            }
        )

    except Exception as e:
        logger.error(f"❌ Error generando TTS: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generando audio: {str(e)}")
