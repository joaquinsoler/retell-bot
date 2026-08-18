import os
import io
import logging
from datetime import datetime

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pypdf import PdfReader
import psycopg2
from psycopg2.extras import RealDictCursor

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

    # Asegurar que existe la columna full_text
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


# ====================== ENDPOINTS ======================

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    name: str = Form(...)
):
    logger.info("=" * 70)
    logger.info("🚀 INICIO DE SUBIDA DE DOCUMENTO")

    try:
        # Validar tipo de archivo
        allowed_types = ["application/pdf", "text/plain"]
        is_pdf = file.content_type == "application/pdf"
        is_txt = file.content_type == "text/plain" or (file.filename and file.filename.lower().endswith(".txt"))

        if not (is_pdf or is_txt):
            logger.error(f"❌ Tipo de archivo no permitido: {file.content_type}")
            raise HTTPException(
                status_code=400, 
                detail="Solo se permiten archivos PDF o TXT"
            )

        final_name = name.strip()
        if not final_name:
            raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")

        logger.info(f"📥 Archivo recibido: {file.filename}")
        logger.info(f"📝 Nombre asignado: {final_name}")
        logger.info(f"📄 Tipo: {'PDF' if is_pdf else 'TXT'}")

        contents = await file.read()
        size_bytes = len(contents)
        logger.info(f"📦 Tamaño: {size_bytes} bytes")

        full_text = ""
        num_pages = 1

        if is_pdf:
            # ===== Procesar PDF =====
            pdf_file = io.BytesIO(contents)
            reader = PdfReader(pdf_file)
            num_pages = len(reader.pages)

            full_text_parts = []
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                full_text_parts.append(page_text)
                logger.info(f"   → Página {i+1}/{num_pages} extraída")

            full_text = "\n\n".join(full_text_parts).strip()

        else:
            # ===== Procesar TXT =====
            try:
                full_text = contents.decode("utf-8")
            except UnicodeDecodeError:
                # Intentar con latin-1 si utf-8 falla
                full_text = contents.decode("latin-1")
            
            # Estimar páginas (aprox. 3000 caracteres por página)
            num_pages = max(1, len(full_text) // 3000)
            logger.info("   → Texto TXT extraído correctamente")

        logger.info(f"📄 Texto completo extraído ({len(full_text)} caracteres)")
        logger.info(f"📄 Páginas estimadas: {num_pages}")

        # ===== Guardar en base de datos =====
        logger.info("💾 Guardando en la base de datos...")

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
        logger.info(f"   → Páginas: {saved['pages']}")
        logger.info(f"   → Tamaño: {saved['size_bytes']} bytes")

        # Verificación
        cur.execute("SELECT id, filename FROM documents WHERE id = %s", (saved['id'],))
        verified = cur.fetchone()

        if verified and verified['filename'] == final_name:
            logger.info("✅ Verificación exitosa")
        else:
            raise Exception("Falló la verificación")

        cur.close()
        conn.close()

        logger.info("🎉 PROCESO COMPLETADO CON ÉXITO")
        logger.info("=" * 70)

        return {
            "status": "success",
            "document": {
                "id": saved['id'],
                "filename": saved['filename'],
                "size_bytes": saved['size_bytes'],
                "pages": saved['pages'],
                "uploaded_at": str(saved['uploaded_at'])
            }
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ ERROR: {str(e)}")
        logger.info("=" * 70)
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

        logger.info(f"📚 Listados {len(documents)} documentos")
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

        logger.info(f"📖 Documento {doc_id} solicitado: {row['filename']}")

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
        logger.error(f"Error obteniendo documento {doc_id}: {e}")
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
