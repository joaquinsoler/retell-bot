import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==================== JWT (python-jose) ====================
from jose import JWTError, jwt   # ← Este es el import correcto

# ==================== CONFIG ====================
app = FastAPI(title="Dansu Backend Completo")

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL]):
    raise Exception("Faltan variables de entorno críticas")

if not JWT_SECRET_KEY:
    raise Exception("Falta JWT_SECRET_KEY en Render")
if not BREVO_API_KEY:
    print("⚠️ BREVO_API_KEY no configurada")

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15

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
    print("✅ Base de datos inicializada")

init_db()

# ==================== GOOGLE CALENDAR (sin cambios) ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

# (Mantengo todas tus funciones de Google Calendar aquí - copia y pega las tuyas originales)
def ensure_calendar_access(calendar_id: str): ...          # ← pega tu función
def normalize_to_madrid_iso(dt_str: str) -> str: ...      # ← pega tu función
def check_availability(...): ...                          # ← pega tu función
def create_google_event(...): ...                         # ← pega tu función

# ==================== VOICE + RETELL (sin cambios) ====================
VOICE_MAPPING = { ... }  # ← tu diccionario original

def retell_request(...): ...          # ← tu función original
def build_custom_prompt(...): ...     # ← tu función original
def create_bot_for_client(...): ...   # ← tu función original

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
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
            json={
                "sender": {"name": "Dansu AI", "email": "no-reply@dansu.info"},
                "to": [{"email": email}],
                "templateId": 1,
                "subject": "Tu enlace de acceso a Dansu AI",
                "params": {"MAGIC_LINK": magic_link}
            }
        )
        success = response.status_code in (200, 201)
        if success:
            print(f"✅ Magic link enviado a {email}")
        else:
            print(f"❌ Brevo: {response.text}")
        return success
    except Exception as e:
        print(f"❌ Error Brevo: {e}")
        return False

# ==================== MAGIC LINK ENDPOINTS ====================
class MagicLinkRequest(BaseModel):
    email: str

@app.post("/send-magic-link")
async def send_magic_link(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email or "@" not in email:
            raise HTTPException(400, "Email inválido")

        token = create_magic_token(email)
        magic_link = f"https://tudominio.com/editar-asistente?token={token}"   # ← CAMBIA ESTA URL

        if send_magic_link_email(email, magic_link):
            return {"status": "success", "message": "Enlace enviado a tu correo"}
        raise HTTPException(500, "No se pudo enviar el email")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/verify-magic-token")
async def verify_magic_token_endpoint(request: Request):
    try:
        data = await request.json()
        token = data.get("token")
        email = verify_magic_token(token)
        if not email:
            raise HTTPException(401, "Enlace inválido o caducado")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()

        return {"status": "success", "email": email, "bots": bots}
    except Exception as e:
        raise HTTPException(401, "Token inválido")

# ==================== TUS ENDPOINTS ORIGINALES ====================
# Pega aquí todos tus endpoints originales:
# /get-user-bots, /update-retell-bot, /delete-retell-bot, /book-appointment, etc.

@app.get("/")
async def root():
    return {"status": "Dansu Backend OK - Magic Link activado ✅"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
