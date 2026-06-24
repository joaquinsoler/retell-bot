import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==================== JWT para Magic Link ====================
from jose import JWTError, jwt

# ==================== CONFIGURACIÓN ====================
app = FastAPI(title="Dansu Backend Completo")

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas")
if not JWT_SECRET_KEY:
    raise Exception("Falta JWT_SECRET_KEY para el sistema de Magic Link")
if not BREVO_API_KEY:
    print("⚠️ Advertencia: BREVO_API_KEY no configurada. Magic Links no funcionarán.")

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Cambia esto en producción por tu dominio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== JWT CONFIG ====================
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15

# ==================== CONEXIÓN POSTGRESQL ====================
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

# ==================== GOOGLE CALENDAR (código original sin cambios) ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=SCOPES
    )
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

# ... (mantengo todas tus funciones originales de Google Calendar sin modificar)
def ensure_calendar_access(calendar_id: str):
    try:
        service = get_calendar_service()
        service.calendarList().insert(body={'id': calendar_id}).execute()
        print(f"✅ Calendario suscrito: {calendar_id}")
    except HttpError as e:
        if e.status_code == 409:
            print(f"ℹ️ Ya suscrito: {calendar_id}")
        else:
            print(f"⚠️ Error suscripción: {e}")

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str:
        return dt_str
    dt_str = str(dt_str).strip().replace(" ", "T")
    if dt_str.endswith("Z"):
        dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC"))
    else:
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
        except ValueError:
            return dt_str
    return dt.astimezone(MADRID_TZ).isoformat()

def check_availability(calendar_id: str, start_time: str, end_time: str) -> bool:
    try:
        service = get_calendar_service()
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        body = {
            "timeMin": iso_start,
            "timeMax": iso_end,
            "timeZone": "Europe/Madrid",
            "items": [{"id": calendar_id}]
        }
        freebusy_query = service.freebusy().query(body=body).execute()
        busy = freebusy_query.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy) == 0
    except Exception:
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        
        if not bypass_availability and not check_availability(calendar_id, iso_start, iso_end):
            raise Exception("El horario seleccionado ya no está disponible.")

        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': description or "Cita agendada por Dansu AI",
            'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        created = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
        print(f"✅ EVENTO CREADO: {created.get('htmlLink')}")
        return created
    except Exception as e:
        print(f"❌ Error Google Calendar: {e}")
        raise

# ==================== VOICE MAPPING & RETELL (código original) ====================
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

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres la voz y el asistente virtual exclusivo de {nombre_negocio}, un negocio enfocado en el sector de {sector}. ..."""  # (tu prompt completo original)

# ==================== MAGIC LINK FUNCTIONS ====================
def create_magic_token(email: str):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    data = {"sub": email.lower(), "exp": expire}
    return jwt.encode(data, JWT_SECRET_KEY, algorithm=ALGORITHM)

def verify_magic_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

def send_magic_link_email(email: str, magic_link: str):
    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "sender": {"name": "Dansu AI", "email": "no-reply@dansu.info"},
                "to": [{"email": email}],
                "templateId": 1,                    # ← Tu template ID
                "subject": "Tu enlace de acceso a Dansu AI",
                "params": {"MAGIC_LINK": magic_link}
            }
        )
        if response.status_code in (201, 200):
            print(f"✅ Magic link enviado a {email}")
            return True
        else:
            print(f"❌ Brevo error: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error enviando magic link: {e}")
        return False

# ==================== CREATE BOT (código original) ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)
    # ... (tu función completa original sin cambios)
    # Retorno final
    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}

# ==================== MAGIC LINK ENDPOINTS ====================
class MagicLinkRequest(BaseModel):
    email: str

class TokenVerify(BaseModel):
    token: str

@app.post("/send-magic-link")
async def send_magic_link(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="Email inválido")

        token = create_magic_token(email)
        # Cambia esta URL por la tuya real (puede ser Wix, Vercel, etc.)
        magic_link = f"https://tudominio.com/editar-asistente?token={token}"

        if send_magic_link_email(email, magic_link):
            return {"status": "success", "message": "Enlace enviado correctamente a tu correo"}
        else:
            raise HTTPException(status_code=500, detail="No se pudo enviar el email")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-magic-token")
async def verify_magic_token_endpoint(request: Request):
    try:
        data = await request.json()
        token = data.get("token")
        if not token:
            raise HTTPException(status_code=400, detail="Token requerido")

        email = verify_magic_token(token)
        if not email:
            raise HTTPException(status_code=401, detail="Enlace inválido o caducado")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()

        return {
            "status": "success",
            "email": email,
            "bots": bots
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

# ==================== TUS ENDPOINTS ORIGINALES (sin cambios) ====================
@app.post("/get-user-bots")
async def get_user_bots(request: Request):
    # ... tu código original
    pass

@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    # ... tu código original completo
    pass

@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    # ... tu código original completo
    pass

@app.post("/book-appointment")
async def book_appointment(request: Request):
    # ... tu código original
    pass

@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    # ... tu código original
    pass

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    # ... tu código original
    pass

@app.get("/")
async def root():
    return {"status": "Dansu Backend Completo OK - Magic Link activado"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
