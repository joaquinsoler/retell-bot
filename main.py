import os
import io
import json
import logging
from typing import List, Optional
from contextlib import contextmanager

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
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
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
@contextmanager
def get_db_cursor():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()

def init_db():
    try:
        with get_db_cursor() as cur:
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
                CREATE TABLE IF NOT EXISTS exercises (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    exercise_number INTEGER NOT NULL,
                    statement TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
        logger.info("✅ Base de datos inicializada")
    except Exception as e:
        logger.error(f"❌ Error inicializando DB: {e}")

@app.on_event("startup")
def startup():
    init_db()

# ====================== MODELOS ======================
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    question: str
    full_text: str
    history: Optional[List[ChatMessage]] = []
    mode: Optional[str] = "doubt"

class RenameRequest(BaseModel):
    name: str

# ====================== UTILIDADES ======================
async def call_grok(messages: list, temperature: float = 0.3) -> str:
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="API Key no configurada")
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            GROK_API_URL,
            headers={"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"},
            json={"model": GROK_MODEL, "messages": messages, "temperature": temperature}
        )
    if response.status_code != 200:
        logger.error(f"Error Grok: {response.text}")
        raise HTTPException(status_code=500, detail="Error en comunicación con IA")
    return response.json()["choices"][0]["message"]["content"]

def escape_context(text: str) -> str:
    return text.replace("=== FIN ===", "").replace("=== EJERCICIO ===", "")

# ====================== ENDPOINTS DOCUMENTOS ======================
@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...), name: str = Form(...), doc_type: str = Form("text"),
    start_page: int = Form(1), end_page: int = Form(None)
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Solo PDF")
    
    contents = await file.read()
    reader = PdfReader(io.BytesIO(contents))
    total_pages = len(reader.pages)
    end_page = end_page or total_pages
    
    if doc_type == "exercise" and (end_page - start_page + 1) > MAX_PAGES_EXERCISES:
        raise HTTPException(status_code=400, detail="Excedido límite de páginas")

    pages_text = "\n\n".join([p.extract_text() or "" for p in reader.pages[start_page-1:end_page]])
    
    with get_db_cursor() as cur:
        cur.execute("""
            INSERT INTO documents (filename, content, full_text, pages, doc_type, start_page, end_page)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (name, contents, pages_text, (end_page-start_page+1), doc_type, start_page, end_page))
        doc_id = cur.fetchone()["id"]
    return {"status": "success", "document": {"id": doc_id}}

@app.get("/api/documents")
async def list_documents(doc_type: Optional[str] = None):
    with get_db_cursor() as cur:
        query = "SELECT id, filename, pages, uploaded_at FROM documents"
        params = []
        if doc_type:
            query += " WHERE doc_type = %s"
            params.append(doc_type)
        cur.execute(query + " ORDER BY uploaded_at DESC", params)
        return {"documents": [{"id": r["id"], "title": r["filename"], "pages": r["pages"], "date": r["uploaded_at"].strftime("%d %b %Y")} for r in cur.fetchall()]}

@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: int):
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        if not row: raise HTTPException(status_code=404, detail="No encontrado")
        return {**row, "uploaded_at": str(row["uploaded_at"])}

@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: int):
    with get_db_cursor() as cur:
        cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
    return {"status": "success"}

@app.put("/api/documents/{doc_id}/rename")
async def rename_document(doc_id: int, request: RenameRequest):
    with get_db_cursor() as cur:
        cur.execute("UPDATE documents SET filename = %s WHERE id = %s", (request.name, doc_id))
    return {"status": "success"}

# ====================== EJERCICIOS Y CHAT ======================
@app.post("/api/exercises/process")
async def process_exercises(document_id: int = Form(...)):
    with get_db_cursor() as cur:
        cur.execute("SELECT full_text FROM documents WHERE id = %s", (document_id,))
        doc = cur.fetchone()
        text = escape_context(doc["full_text"])
        
        system_prompt = "Extrae ejercicios en formato JSON: {'exercises': [{'number': 1, 'statement': '...'}]}"
        response = await call_grok([{"role": "system", "content": system_prompt}, {"role": "user", "content": text}])
        
        try:
            data = json.loads(response.replace("```json", "").replace("```", ""))
            for ex in data.get("exercises", []):
                cur.execute("INSERT INTO exercises (document_id, exercise_number, statement) VALUES (%s, %s, %s)", 
                            (document_id, ex["number"], ex["statement"]))
        except Exception:
            raise HTTPException(status_code=422, detail="invalid_content")
    return {"status": "success"}

@app.get("/api/exercises/{document_id}")
async def list_exercises(document_id: int):
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM exercises WHERE document_id = %s ORDER BY exercise_number", (document_id,))
        return {"exercises": [{"id": r["id"], "number": r["exercise_number"], "statement": r["statement"]} for r in cur.fetchall()]}

@app.post("/api/chat")
async def chat(request: ChatRequest):
    safe_text = escape_context(request.full_text)
    system_content = f"Eres un profesor. Ejercicio: {safe_text}"
    messages = [{"role": "system", "content": system_content}]
    for msg in request.history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": request.question})
    
    answer = await call_grok(messages)
    return {"answer": answer}
