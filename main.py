import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import resend
from jose import JWTError, jwt
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas (RETELL_API_KEY, GOOGLE_CREDENTIALS o DATABASE_URL)")

if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY
else:
    print("⚠️ RESEND_API_KEY no configurada")

# ==================== CORS (Importante para que funcione el frontend) ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== JWT ====================
ALGORITHM = "HS256"
security = HTTPBearer()

def create_magic_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode = {"sub": email.lower().strip(), "exp": expire, "type": "magic"}
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

# ==================== MODELO ====================
class MagicLinkRequest(BaseModel):
    email: str

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
    print("✅ Base de datos inicializada.")

init_db()

# ==================== GOOGLE CALENDAR (tu código original) ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

# ... (Mantengo todas tus funciones originales: ensure_calendar_access, normalize_to_madrid_iso, check_availability, create_google_event)

def ensure_calendar_access(calendar_id: str):
    try:
        service = get_calendar_service()
        service.calendarList().insert(body={'id': calendar_id}).execute()
    except HttpError as e:
        if e.status_code != 409:
            print(f"⚠️ Error suscripción calendario: {e}")

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

# (Pega aquí el resto de tus funciones originales: check_availability, create_google_event, etc.)

# ==================== VOICE MAPPING Y RETELL (tu código original) ====================
VOICE_MAPPING = { ... }  # ← pega tu diccionario completo aquí

def retell_request(method: str, endpoint: str, json_data=None):
    # tu función original
    ...

def build_custom_prompt(...):
    # tu función original completa
    ...

def create_bot_for_client(...):
    # tu función original completa
    ...

# ==================== NUEVOS ENDPOINTS ====================
@app.post("/auth/magic-link")
async def send_magic_link(request: MagicLinkRequest):
    email = request.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as count FROM asistentes WHERE google_calendar_email = %s", (email,))
    result = cur.fetchone()
    cur.close()
    conn.close()

    if result['count'] == 0:
        return {"status": "success"}

    token = create_magic_token(email)
    login_url = f"https://www.dansu.info/area-cliente?token={token}"

    try:
        resend.Emails.send({
            "from": "Dansu <no-reply@dansu.info>",
            "to": email,
            "subject": "Tu enlace para acceder al panel de Dansu",
            "html": f"""
                <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 30px;">
                    <h2 style="color: #0078FF;">Bienvenido a Dansu</h2>
                    <p>Haz clic en el botón para entrar a tu panel:</p>
                    <a href="{login_url}" style="display: inline-block; background: #0078FF; color: white; padding: 16px 32px; border-radius: 8px; text-decoration: none; font-weight: bold;">
                        Entrar al Panel Dansu
                    </a>
                    <p style="color: #666; font-size: 14px; margin-top: 25px;">Este enlace caduca en 15 minutos por seguridad.</p>
                </div>
            """
        })
        return {"status": "success", "message": "Enlace enviado"}
    except Exception as e:
        print(f"❌ Error Resend: {e}")
        raise HTTPException(status_code=500, detail="Error al enviar el email")


@app.post("/get-user-bots")
async def get_user_bots(user_email: str = Depends(get_current_user)):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (user_email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()
        return {"status": "success", "bots": bots}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== TUS ENDPOINTS ORIGINALES (sin protección todavía) ====================
# (pega aquí update-retell-bot, delete-retell-bot, book-appointment, verify-calendar-access, create-retell-bot, etc.)

@app.get("/")
async def root():
    return {"status": "Dansu Backend Completo OK"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
