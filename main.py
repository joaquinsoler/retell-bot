import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Dansu Backend Completo")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas")

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== POSTGRESQL ====================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS asistentes (
            id SERIAL PRIMARY KEY,
            nombre_negocio VARCHAR(255),
            sector VARCHAR(255),
            servicios TEXT,
            horario VARCHAR(255),
            zona VARCHAR(255),
            google_calendar_email VARCHAR(255),
            asistente VARCHAR(255),
            agent_id VARCHAR(255) UNIQUE,
            phone_number VARCHAR(255),
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Base de datos PostgreSQL inicializada.")

init_db()

# ==================== GOOGLE CALENDAR (sin cambios) ====================
# ... (todo tu código de Google Calendar se mantiene igual) ...

SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

# ... (mantengo todas tus funciones de Google Calendar: ensure_calendar_access, normalize_to_madrid_iso, check_availability, create_google_event) ...

# ==================== RETELL UTILS (sin cambios) ====================
VOICE_MAPPING = {
    "Cimo": "11labs-Adrian", "Brynne": "11labs-Brynne", "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova", "Grace": "openai-Shimmer", "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa", "Lily": "11labs-Lily", "Della": "11labs-Delia",
    "Nico": "openai-Onyx", "Rita": "11labs-Rita", "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa", "Maren": "11labs-Maren", "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley", "Andrea": "openai-Alloy", "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby", "Alejandro": "openai-Echo", "Sloane": "11labs-Sloane"
}

def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        print(f"→ Retell {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Error Retell: {e}")
        return None

def build_custom_prompt(...):  # se mantiene igual
    ...

def create_bot_for_client(...):  # se mantiene igual
    ...

# ==================== ENDPOINTS ====================

@app.post("/get-user-bots")
async def get_user_bots(request: Request):
    # ... (se mantiene igual)
    ...

# ====================== ENDPOINT DE EDICIÓN SEGURO ======================
@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    """
    VERSIÓN SEGURA - SOLO ACTUALIZA LA BASE DE DATOS
    NO toca Retell AI, ni prompt, ni LLM, ni agente.
    """
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el agent_id")

        # Solo actualizamos los campos en PostgreSQL
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio = %s,
                sector = %s,
                servicios = %s,
                horario = %s,
                zona = %s,
                google_calendar_email = %s,
                asistente = %s
            WHERE agent_id = %s;
        """, (
            data.get("nombre_negocio", "").strip(),
            data.get("sector", "").strip(),
            data.get("servicios", "").strip(),
            data.get("horario", "").strip(),
            data.get("zona", "").strip(),
            data.get("google_calendar_email", "").strip(),
            data.get("asistente", "").strip(),           # ← Nueva voz
            agent_id
        ))

        if cur.rowcount == 0:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Asistente no encontrado")

        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "message": "Datos actualizados correctamente en la base de datos"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error en update-retell-bot (seguro): {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    # ... (se mantiene exactamente igual)
    ...

@app.post("/book-appointment")
async def book_appointment(request: Request):
    # ... (se mantiene igual)
    ...

@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    # ... (se mantiene igual)
    ...

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    # ... (se mantiene igual)
    ...

@app.get("/")
async def root():
    return {"status": "Dansu Backend Completo OK"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
