import os
import io
import logging
from datetime import datetime

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pypdf import PdfReader
import psycopg2
from psycopg2.extras import RealDictCursor

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

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
    """Crea la tabla si no existe"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL,
            content BYTEA NOT NULL,
            size_bytes INTEGER,
            pages INTEGER,
            first_paragraph TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ Tabla 'documents' verificada/creada correctamente")


# Crear la tabla al arrancar el servidor
@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:
        logger.error(f"❌ Error al inicializar la base de datos: {e}")


# ====================== ENDPOINT ======================

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    logger.info("=" * 70)
    logger.info("🚀 INICIO DEL PROCESO DE SUBIDA DE PDF")

    # 1. Validar que sea PDF
    if file.content_type != "application/pdf":
        logger.error("❌ El archivo no es un PDF")
        raise HTTPException(status_code=400, detail="Solo se permiten archivos PDF")

    try:
        # 2. Leer el contenido del archivo
        contents = await file.read()
        size_bytes = len(contents)
        logger.info(f"📥 PDF recibido: {file.filename} ({size_bytes} bytes)")

        # 3. Extraer información del PDF
        pdf_file = io.BytesIO(contents)
        reader = PdfReader(pdf_file)
        num_pages = len(reader.pages)

        first_page_text = ""
        if num_pages > 0:
            first_page_text = reader.pages[0].extract_text() or ""

        # Obtener primer párrafo
        paragraphs = [p.strip() for p in first_page_text.split("\n\n") if p.strip()]
        if paragraphs:
            first_paragraph = paragraphs[0]
        else:
            lines = [line.strip() for line in first_page_text.split("\n") if line.strip()]
            first_paragraph = " ".join(lines[:5]) if lines else "No se pudo extraer texto"

        logger.info(f"📄 Páginas detectadas: {num_pages}")
        logger.info(f"📝 Primer párrafo extraído correctamente")

        # 4. Guardar en la base de datos
        logger.info("💾 Guardando PDF en la base de datos...")

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO documents (filename, content, size_bytes, pages, first_paragraph)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, filename, size_bytes, pages, uploaded_at;
        """, (file.filename, contents, size_bytes, num_pages, first_paragraph))

        saved_doc = cur.fetchone()
        conn.commit()

        logger.info(f"✅ PDF guardado correctamente en la base de datos")
        logger.info(f"   → ID asignado: {saved_doc['id']}")
        logger.info(f"   → Nombre: {saved_doc['filename']}")
        logger.info(f"   → Tamaño: {saved_doc['size_bytes']} bytes")
        logger.info(f"   → Páginas: {saved_doc['pages']}")
        logger.info(f"   → Fecha: {saved_doc['uploaded_at']}")

        # 5. Verificación: leer de nuevo desde la base de datos
        logger.info("🔍 Verificando que el PDF se guardó correctamente...")

        cur.execute("""
            SELECT id, filename, size_bytes, pages, first_paragraph, uploaded_at
            FROM documents
            WHERE id = %s;
        """, (saved_doc['id'],))

        verified = cur.fetchone()

        if verified and verified['size_bytes'] == size_bytes:
            logger.info("✅ VERIFICACIÓN EXITOSA: El PDF se recuperó correctamente de la base de datos")
            logger.info(f"   → Primer párrafo guardado: {verified['first_paragraph'][:150]}...")
        else:
            logger.error("❌ Error en la verificación: no se pudo recuperar el PDF correctamente")
            raise Exception("Falló la verificación del guardado")

        cur.close()
        conn.close()

        logger.info("🎉 PROCESO COMPLETADO CON ÉXITO")
        logger.info("=" * 70)

        return {
            "status": "success",
            "message": "PDF guardado y verificado correctamente",
            "document": {
                "id": verified['id'],
                "filename": verified['filename'],
                "size_bytes": verified['size_bytes'],
                "pages": verified['pages'],
                "first_paragraph": verified['first_paragraph'],
                "uploaded_at": str(verified['uploaded_at'])
            }
        }

    except Exception as e:
        logger.error(f"❌ ERROR GENERAL: {str(e)}")
        logger.info("=" * 70)
        raise HTTPException(status_code=500, detail=f"Error procesando el PDF: {str(e)}")
