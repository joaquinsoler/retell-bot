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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("DansuAI-Backend")

app = FastAPI(title="Dansu Backend - Pronunciación Mejorada")

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    logger.critical("Faltan variables de entorno críticas.")
    raise Exception("Faltan variables de entorno críticas")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
SESIONES_ACTIVAS = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS asistentes (
                id SERIAL PRIMARY KEY,
                nombre_negocio VARCHAR(255),
                sector VARCHAR(255),
                servicios TEXT,
                horario VARCHAR(255),
                duracion_cita INT DEFAULT 30,
                zona VARCHAR(255),
                google_calendar_email VARCHAR(255),
                asistente VARCHAR(255),
                agent_id VARCHAR(255) UNIQUE,
                phone_number VARCHAR(255),
                idioma VARCHAR(50) DEFAULT 'es',
                datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita',
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS idioma VARCHAR(50) DEFAULT 'es';")
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita';")
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS duracion_cita INT DEFAULT 30;")
        conn.commit()
        logger.info("✅ Base de datos inicializada.")
    except Exception as e:
        logger.error(f"❌ Error inicializando DB: {e}", exc_info=True)
    finally:
        cur.close()
        conn.close()

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
            logger.error(f"Error suscripción: {e}")

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
        body = {
            "timeMin": normalize_to_madrid_iso(start_time),
            "timeMax": normalize_to_madrid_iso(end_time),
            "timeZone": "Europe/Madrid",
            "items": [{"id": calendar_id}]
        }
        freebusy_query = service.freebusy().query(body=body).execute()
        busy = freebusy_query.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy) == 0
    except Exception as e:
        logger.error(f"Error FreeBusy: {e}", exc_info=True)
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    ensure_calendar_access(calendar_id)
    iso_start = normalize_to_madrid_iso(start_time)
    iso_end = normalize_to_madrid_iso(end_time)
    if not bypass_availability and not check_availability(calendar_id, iso_start, iso_end):
        raise Exception("Horario no disponible")
    service = get_calendar_service()
    event = {
        'summary': summary[:100],
        'description': description or "Cita agendada por Dansu AI",
        'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
        'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
        'reminders': {'useDefault': True}
    }
    return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()

# ==================== RETELL ====================
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
        logger.info(f"→ Retell {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        logger.error(f"Error Retell: {e}", exc_info=True)
        return None

# ==================== PROMPT REFORZADO ====================
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

**REFERENCIA TEMPORAL:**
- Hoy es: **{fecha_legible}**. Hora: **{hora_legible}**.

**REGLA ABSOLUTA DE PRONUNCIACIÓN (SIEMPRE):**
Usa grupos de tres dígitos separados por " - ".
Pronuncia **todo** el número completo.
Ejemplos:
- 611223344 → "seis uno uno - dos dos tres - tres cuatro cuatro"
- 622334455 → "seis dos dos - tres tres cuatro - cuatro cinco cinco"
- 655112233 → "seis cinco cinco - uno uno dos - dos tres tres"
Repite siempre el teléfono completo en este formato.

**IDIOMA:** Siempre en **{idioma_atencion}**.

**ALCANCE, PERSONALIDAD Y FLUJO:** (el resto igual que tu prompt original)"""

# ==================== CREACIÓN ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email, 
                          idioma="es", datos_reserva="Nombre completo, Número de teléfono, Motivo de la cita", duracion_cita=30):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma, datos_reserva)

    retell_language_mapping = {"es": "es-ES", "en": "en-US", "ca": "ca-ES"}
    lang_retell = retell_language_mapping.get(str(idioma).strip().lower(), "es-ES")

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda la cita.",
            "url": "https://retell-bot.onrender.com/book-appointment",
            "method": "POST",
            "parameters": {
                "type": "object",
                "properties": {
                    "calendar_email": {"type": "string"},
                    "summary": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "description": {"type": "string"},
                    "datos_cliente_recolectados": {"type": "string"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time", "datos_cliente_recolectados"]
            }
        }]
    })

    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": lang_retell,
        "handbook_config": {"speech_normalization": True}
    })

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent")

    agent_id = agent_res["agent_id"]

    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents"):
                free_number = p.get("phone_number")
                break

    if free_number:
        retell_request("PATCH", f"/update-phone-number/{free_number}", {"inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]})

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, duracion_cita, zona, google_calendar_email, asistente, agent_id, phone_number, idioma, datos_reserva)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """, (nombre_negocio, sector, servicios, horario, duracion_cita, zona, calendar_email, voice_id, agent_id, free_number, idioma, datos_reserva))
        conn.commit()
    finally:
        cur.close()
        conn.close()

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}

# ==================== UPDATE ====================
@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        # ... (extrae todos los campos como en tu código original)
        nuevo_prompt = build_custom_prompt(...)  # usa los datos recibidos

        retell_request("PATCH", f"/update-retell-llm/{llm_id}", {"general_prompt": nuevo_prompt, "general_tools": [...]})  # mantén tus tools

        agent_patch_data = {
            "language": lang_retell,
            "handbook_config": {"speech_normalization": True}
        }
        if voice_id_tecnico:
            agent_patch_data["voice_id"] = voice_id_tecnico

        retell_request("PATCH", f"/update-agent/{agent_id}", agent_patch_data)

        # actualización DB (igual que antes)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(500, str(e))

# (El resto de endpoints — magic link, book-appointment, delete, etc. — cópialos tal cual de tu archivo original)

@app.get("/")
async def root():
    return {"status": "OK - Pronunciación reforzada"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
