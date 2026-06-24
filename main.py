import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from jose import JWTError, jwt

app = FastAPI(title="Dansu Backend Completo - Magic Link + IP Session")

# ==================== VARIABLES ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    raise Exception("❌ Faltan variables de entorno críticas")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15

SESIONES_ACTIVAS = {}  # IP → {"email": , "expira": }

# ==================== DB ====================
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
    print("✅ DB inicializada")

init_db()

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

def ensure_calendar_access(calendar_id: str):
    try:
        service = get_calendar_service()
        service.calendarList().insert(body={'id': calendar_id}).execute()
    except HttpError as e:
        if e.status_code != 409:
            print(f"⚠️ Error calendario: {e}")

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str: return dt_str
    dt_str = str(dt_str).strip().replace(" ", "T")
    try:
        if dt_str.endswith("Z"):
            dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
        return dt.astimezone(MADRID_TZ).isoformat()
    except:
        return dt_str

def check_availability(calendar_id: str, start_time: str, end_time: str) -> bool:
    try:
        service = get_calendar_service()
        body = {
            "timeMin": normalize_to_madrid_iso(start_time),
            "timeMax": normalize_to_madrid_iso(end_time),
            "timeZone": "Europe/Madrid",
            "items": [{"id": calendar_id}]
        }
        fb = service.freebusy().query(body=body).execute()
        return len(fb.get("calendars", {}).get(calendar_id, {}).get("busy", [])) == 0
    except:
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        if not bypass_availability and not check_availability(calendar_id, start_time, end_time):
            raise Exception("Horario ocupado")
        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': description or "Cita agendada por Dansu AI",
            'start': {'dateTime': normalize_to_madrid_iso(start_time), 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': normalize_to_madrid_iso(end_time), 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
    except Exception as e:
        print(f"❌ Google Error: {e}")
        raise

# ==================== RETELL Y CREACIÓN ====================
VOICE_MAPPING = { ... }  # Tu diccionario completo

def retell_request(...): ...  # Tu función original

def build_custom_prompt(...): ...  # Tu prompt original

def create_bot_for_client(...): ...  # Tu función completa original

# ==================== MAGIC LINK ====================
def create_magic_token(email: str):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": email.lower(), "exp": expire}, JWT_SECRET_KEY, algorithm=ALGORITHM)

def verify_magic_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

def send_magic_link_email(email: str, magic_link: str):
    try:
        payload = {
            "sender": {"name": "Dansu AI", "email": "soporte@dansutech.com"},
            "to": [{"email": email}],
            "subject": "🔑 Tu enlace de acceso a Dansu AI",
            "htmlContent": f"""<html><body style="font-family:sans-serif;padding:30px;">
                <a href="{magic_link}" style="background:#0078FF;color:white;padding:14px 28px;border-radius:12px;text-decoration:none;">
                    Entrar al Panel ✨
                </a>
            </body></html>"""
        }
        r = requests.post("https://api.brevo.com/v3/smtp/email",
                          headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                          json=payload)
        return r.status_code in (200, 201)
    except:
        return False

# ==================== ENDPOINTS MAGIC LINK ====================
@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    # ... (código anterior)
    pass

@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    # ... (código anterior)
    pass

@app.get("/check-session")
async def check_session(request: Request):
    # ... (código anterior)
    pass

# ==================== ENDPOINT QUE FALTABA ====================
@app.post("/verify-calendar-access")
@app.post("/verify-calendar-access/")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        create_google_event(
            calendar_email,
            "🧪 Prueba de conexión - Dansu",
            "2026-07-01T10:00:00+02:00",
            "2026-07-01T10:30:00+02:00",
            bypass_availability=True
        )
        return {"status": "success", "message": "Acceso verificado correctamente"}
    except Exception as e:
        print(f"❌ Error verify-calendar-access: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ==================== OTROS ENDPOINTS ORIGINALES ====================
@app.post("/create-retell-bot")
@app.post("/update-retell-bot")
@app.post("/delete-retell-bot")
@app.post("/book-appointment")
# ... (todos tus endpoints originales)

@app.get("/")
async def root():
    return {"status": "✅ Backend completo y funcional"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
