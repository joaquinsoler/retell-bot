import os
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from jose import JWTError, jwt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s", handlers=[logging.StreamHandler()])
logger = logging.getLogger("DansuAI-Backend")

app = FastAPI(title="Dansu Backend - Pronunciación Mejorada")

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    raise Exception("Faltan variables de entorno críticas")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
SESIONES_ACTIVAS = {}

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""CREATE TABLE IF NOT EXISTS asistentes (
            id SERIAL PRIMARY KEY, nombre_negocio VARCHAR(255), sector VARCHAR(255), servicios TEXT,
            horario VARCHAR(255), duracion_cita INT DEFAULT 30, zona VARCHAR(255),
            google_calendar_email VARCHAR(255), asistente VARCHAR(255), agent_id VARCHAR(255) UNIQUE,
            phone_number VARCHAR(255), idioma VARCHAR(50) DEFAULT 'es',
            datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita',
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );""")
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS idioma VARCHAR(50) DEFAULT 'es';")
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita';")
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS duracion_cita INT DEFAULT 30;")
        conn.commit()
        logger.info("✅ Base de datos inicializada.")
    except Exception as e:
        logger.error(f"Error DB: {e}", exc_info=True)
    finally:
        cur.close()
        conn.close()

init_db()

# ==================== GOOGLE CALENDAR (sin cambios) ====================
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
            logger.error(f"Error calendario: {e}")

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str: return dt_str
    dt_str = str(dt_str).strip().replace(" ", "T")
    if dt_str.endswith("Z"):
        dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC"))
    else:
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=MADRID_TZ)
        except ValueError: return dt_str
    return dt.astimezone(MADRID_TZ).isoformat()

def check_availability(calendar_id: str, start_time: str, end_time: str) -> bool:
    try:
        service = get_calendar_service()
        body = {"timeMin": normalize_to_madrid_iso(start_time), "timeMax": normalize_to_madrid_iso(end_time),
                "timeZone": "Europe/Madrid", "items": [{"id": calendar_id}]}
        freebusy = service.freebusy().query(body=body).execute()
        return len(freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])) == 0
    except Exception:
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    ensure_calendar_access(calendar_id)
    iso_start = normalize_to_madrid_iso(start_time)
    iso_end = normalize_to_madrid_iso(end_time)
    if not bypass_availability and not check_availability(calendar_id, iso_start, iso_end):
        raise Exception("Horario no disponible")
    service = get_calendar_service()
    event = {'summary': summary[:100], 'description': description or "Cita agendada por Dansu AI",
             'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
             'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'}, 'reminders': {'useDefault': True}}
    return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()

# ==================== RETELL ====================
VOICE_MAPPING = { ... }  # (mantén tu diccionario original completo)

def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
    return r.json() if r.ok else None

# ==================== PROMPT MEJORADO (OFICIAL + ULTRA REFORZADO) ====================
def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma="es", 
                        datos_reserva="Nombre completo, Número de teléfono, Motivo de la cita"):
    idiomas_legibles = {"es": "Español de España (es-ES)", "en": "Inglés (en-US)", "ca": "Catalán (ca-ES)"}
    idioma_atencion = idiomas_legibles.get(str(idioma).strip().lower(), "Español de España (es-ES)")

    ahora_madrid = datetime.now(MADRID_TZ)
    dias_semana = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"}
    meses_año = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"}
    fecha_legible = f"{dias_semana[ahora_madrid.weekday()]}, {ahora_madrid.day} de {meses_año[ahora_madrid.month]} de {ahora_madrid.year}"
    hora_legible = ahora_madrid.strftime("%H:%M")

    return f"""Eres la voz y el asistente virtual exclusivo de {nombre_negocio}...

**REFERENCIA TEMPORAL OBLIGATORIA:**
- Hoy es: **{fecha_legible}**. Hora actual: **{hora_legible}** (Europe/Madrid).

**REGLA ABSOLUTA #1 - PRONUNCIACIÓN DE TELÉFONOS (APLICAR SIEMPRE):**
Usa Speech Normalization + Read Slowly.
Agrupa SIEMPRE los teléfonos de 9 dígitos en **tres grupos de tres** separados por " - " (con espacios).
**Nunca** te detengas después del primer grupo. Pronuncia **el número completo entero**.
Ejemplos obligatorios:
- 611223344 → "seis uno uno - dos dos tres - tres cuatro cuatro"
- 622334455 → "seis dos dos - tres tres cuatro - cuatro cinco cinco"
- 655112233 → "seis cinco cinco - uno uno dos - dos tres tres"
- 666777888 → "seis seis seis - siete siete siete - ocho ocho ocho"
Cuando confirmes un teléfono, repite **siempre el número completo** en este formato exacto.

**IDIOMA:** Habla siempre en **{idioma_atencion}**.

**ALCANCE Y PERSONALIDAD:** (mantén el resto del prompt original o el que prefieras)

**REGLAS DE ERRORES:** Nunca hables de código ni errores técnicos."""

# ==================== CREACIÓN DEL BOT (CON HANDBOOK_CONFIG) ====================
def create_bot_for_client(...):  # (parámetros iguales)
    custom_prompt = build_custom_prompt(...)

    llm_res = retell_request("POST", "/create-retell-llm", {"model": "gpt-4o-mini", "general_prompt": custom_prompt, ...})

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": lang_retell,
        "handbook_config": {"speech_normalization": True}   # ← ACTIVADO
    })

    # (resto del código de asignación de número y guardado en DB igual)

# ==================== UPDATE (CON HANDBOOK_CONFIG FORZADO) ====================
@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    # ... (código anterior)
    nuevo_prompt = build_custom_prompt(...)

    retell_request("PATCH", f"/update-retell-llm/{llm_id}", {"general_prompt": nuevo_prompt, ...})

    agent_patch_data = {
        "language": lang_retell,
        "handbook_config": {"speech_normalization": True},   # ← FORZADO
    }
    if voice_id_tecnico:
        agent_patch_data["voice_id"] = voice_id_tecnico

    retell_request("PATCH", f"/update-agent/{agent_id}", agent_patch_data)
    # (resto del código de actualización de DB igual)

# (El resto del archivo — delete, book-appointment, create-retell-bot, etc. — se mantiene exactamente igual que tu versión original)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
