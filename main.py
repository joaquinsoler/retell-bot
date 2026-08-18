from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import io
from pypdf import PdfReader   # o PyPDF2 si prefieres
import logging

# Configurar logging para que se vea claramente en Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS (importante porque el frontend está en Wix)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Luego puedes restringirlo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    # 1. Validar que sea un PDF
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Solo se permiten archivos PDF")

    try:
        # 2. Leer el contenido del archivo
        contents = await file.read()
        
        # 3. Extraer texto del PDF
        pdf_file = io.BytesIO(contents)
        reader = PdfReader(pdf_file)
        
        # Extraer texto de la primera página
        first_page_text = ""
        if len(reader.pages) > 0:
            first_page_text = reader.pages[0].extract_text() or ""
        
        # 4. Obtener el primer párrafo
        # (separamos por doble salto de línea o por punto + mayúscula)
        paragraphs = [p.strip() for p in first_page_text.split("\n\n") if p.strip()]
        
        if not paragraphs:
            # Si no hay dobles saltos, cogemos las primeras líneas
            lines = [line.strip() for line in first_page_text.split("\n") if line.strip()]
            first_paragraph = " ".join(lines[:4]) if lines else "No se pudo extraer texto"
        else:
            first_paragraph = paragraphs[0]

        # 5. Mensajes en los logs de Render
        logger.info("=" * 60)
        logger.info("✅ PDF RECIBIDO CON ÉXITO")
        logger.info(f"Nombre del archivo: {file.filename}")
        logger.info(f"Tamaño: {len(contents)} bytes")
        logger.info(f"Número de páginas: {len(reader.pages)}")
        logger.info("-" * 60)
        logger.info("📄 PRIMER PÁRRAFO DEL PDF:")
        logger.info(first_paragraph)
        logger.info("=" * 60)

        return {
            "status": "success",
            "filename": file.filename,
            "pages": len(reader.pages),
            "first_paragraph": first_paragraph
        }

    except Exception as e:
        logger.error(f"❌ Error procesando el PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error procesando el PDF: {str(e)}")
