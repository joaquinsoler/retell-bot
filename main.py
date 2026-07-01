import os
import json
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from threading import Lock
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

app = FastAPI(title="Dansu Backend V5 - Ultra Logging para debug en Render")

# ==================== LOGGING ULTRA ROBUSTO ====================
def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{ts}] [{level}] {msg}")

def log_error(msg: str, exc: Exception = None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if exc:
        print(f"[{ts}] [ERROR] {msg}\n{traceback.format_exc()}")
    else:
        print(f"[{ts}] [ERROR] {msg}")

# ==================== VARIABLES DE ENTORNO ====================
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

# ==================== LOCKS POR CALENDARIO ====================
CALENDAR_LOCKS = {}
def get_calendar_lock(calendar_email: str) -> Lock:
    if calendar_email not in CALENDAR_LOCKS:
        CALENDAR_LOCKS[calendar_email] = Lock()
    return CALENDAR_LOCKS[calendar_email]

# ==================== CORS ====================
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
    log("Base de datos PostgreSQL inicializada.")

init_db()

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    credentials = credentials.with_scopes(SCOPES)
    if hasattr(credentials, 'with_subject'):
        credentials = credentials.with_subject(None)
    if hasattr(credentials, '_regional_access_boundary'):
        credentials._regional_access_boundary = None
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

def ensure_calendar_access(calendar_id: str):
    try:
        service = get_calendar_service()
        service.calendarList().insert(body={'id': calendar_id}).execute()
    except HttpError as e:
        if e.status_code != 409:
            log_error(f"Error al suscribir calendario {calendar_id}", e)

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str:
        return dt_str
    original = str(dt_str).strip()
    cleaned = original.replace(" ", "T")

    try:
        if cleaned.endswith("Z"):
            dt = datetime.fromisoformat(cleaned[:-1]).replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt = datetime.fromisoformat(cleaned)
            if getattr(dt, 'tzinfo', None) is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
        return dt.astimezone(MADRID_TZ).isoformat()
    except (ValueError, TypeError):
        pass

    try:
        if "T" in cleaned and cleaned.count(":") == 1:
            dt = datetime.fromisoformat(cleaned + ":00")
            if getattr(dt, 'tzinfo', None) is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
            return dt.astimezone(MADRID_TZ).isoformat()
    except:
        pass

    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"]:
        try:
            dt = datetime.strptime(original, fmt)
            if getattr(dt, 'tzinfo', None) is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
            return dt.astimezone(MADRID_TZ).isoformat()
        except ValueError:
            continue

    log(f"No se pudo normalizar datetime: {original}", "WARN")
    return cleaned

def check_availability(calendar_id: str, start_time: str, end_time: str) -> bool:
    try:
        service = get_calendar_service()
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        
        log(f"[GOOGLE] FreeBusy check para {calendar_id} | {iso_start} → {iso_end}")
        
        body = {
            "timeMin": iso_start,
            "timeMax": iso_end,
            "timeZone": "Europe/Madrid",
            "items": [{"id": calendar_id}]
        }
        freebusy = service.freebusy().query(body=body).execute()
        busy = freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        
        if busy:
            log(f"[GOOGLE] ❌ Ocupado. Conflictos: {busy}", "WARN")
            return False
        log("[GOOGLE] ✅ Disponible")
        return True
    except Exception as e:
        log_error(f"[GOOGLE] Error en FreeBusy para {calendar_id}", e)
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    lock = get_calendar_lock(calendar_id)
    log(f"[LOCK] Intentando adquirir lock para calendario: {calendar_id}")
    
    with lock:
        log(f"[LOCK] ✅ Lock adquirido para {calendar_id}")
        try:
            ensure_calendar_access(calendar_id)
            iso_start = normalize_to_madrid_iso(start_time)
            iso_end = normalize_to_madrid_iso(end_time)

            log(f"[GOOGLE] Intentando crear evento | start={iso_start} | end={iso_end}")

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
            log(f"[GOOGLE] ✅ Evento creado exitosamente: {created.get('htmlLink')}")
            return created
        except Exception as e:
            log_error(f"[GOOGLE] Error creando evento para {calendar_id}", e)
            raise
        finally:
            log(f"[LOCK] Liberando lock para {calendar_id}")

# ==================== RETELL ====================
VOICE_MAPPING = { ... }  # (se mantiene igual que en V4)

def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        log(f"[RETELL] {method} {endpoint} → Status: {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        log_error(f"[RETELL] Error en {endpoint}", e)
        return None

def build_custom_prompt(...):  # (se mantiene igual)

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    log(f"[RETELL] Iniciando creación de bot para: {nombre_negocio} | Calendar: {calendar_email}")
    
    custom_prompt = build_custom_prompt(...)
    
    tool_def = { ... }  # (definición mejorada)

    llm_payload = {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [tool_def],
        "start_speaker": "agent",
        "model_temperature": 0.1
    }
    log(f"[RETELL] Creando LLM con payload: {json.dumps(llm_payload, indent=2)[:500]}...")
    
    llm_res = retell_request("POST", "/create-retell-llm", llm_payload)

    if not llm_res or "llm_id" not in llm_res:
        log_error("[RETELL] Fallo al crear LLM")
        raise Exception("Error creando LLM en Retell")

    log(f"[RETELL] LLM creado correctamente. llm_id: {llm_res['llm_id']}")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES"
    })

    if not agent_res or "agent_id" not in agent_res:
        log_error("[RETELL] Fallo al crear Agent")
        raise Exception("Error creando Agent en Retell")

    agent_id = agent_res["agent_id"]
    log(f"[RETELL] Agent creado. agent_id: {agent_id}")

    # Asignación de número (con logs)
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents"):
                free_number = p.get("phone_number")
                break

    if free_number:
        log(f"[RETELL] Asignando número {free_number} al agent {agent_id}")
        retell_request("PATCH", f"/update-phone-number/{free_number}", {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
        })

    # Guardar en DB
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(""" ... """, (...))
    conn.commit()
    cur.close()
    conn.close()
    
    log(f"[RETELL] Bot creado exitosamente. agent_id={agent_id}, phone={free_number}")
    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}

# ==================== MAGIC LINK (sin cambios importantes) ====================
# (Se mantienen los endpoints de Magic Link con logs básicos)

# ==================== BOOK-APPOINTMENT CON LOGS ULTRA ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        
        log(f"[BOOK] ===== NUEVA LLAMADA A book-appointment =====")
        log(f"[BOOK] Raw body recibido: {raw_body[:800]}")
        
        args = data.get("args", data)
        log(f"[BOOK] Args parseados: {args}")

        calendar_email = args.get("calendar_email")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        summary = args.get("summary", "Cita")

        if not all([calendar_email, start_time, end_time]):
            log("[BOOK] ERROR: Faltan parámetros obligatorios", "ERROR")
            return {"code": "ERROR", "message": "ERROR_FORMATO_HORA: Faltan parámetros obligatorios."}

        log(f"[BOOK] Calendar: {calendar_email} | Start: {start_time} | End: {end_time}")

        event = create_google_event(calendar_email, summary, start_time, end_time, args.get("description", ""))
        log(f"[BOOK] ✅ Reserva completada con éxito para {calendar_email}")
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}

    except Exception as e:
        log_error(f"[BOOK] Excepción en book-appointment para calendar {args.get('calendar_email', 'UNKNOWN')}", e)
        error_msg = str(e)
        error_lower = error_msg.lower()

        if "ya no está disponible" in error_lower or "ocupado" in error_lower:
            return {"code": "ERROR", "message": "SLOT_OCUPADO: El horario ya no está disponible."}
        elif any(kw in error_lower for kw in ["datetime", "format", "iso", "parse"]):
            return {"code": "ERROR", "message": "ERROR_FORMATO_HORA: Formato de fecha/hora inválido."}
        else:
            return {"code": "ERROR", "message": f"Error técnico: {error_msg[:300]}"}

# ==================== OTROS ENDPOINTS CON LOGS ====================
@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    payload = await request.json()
    log(f"[CREATE] Payload recibido para crear bot: {payload}")
    # ... resto del código con logs ...

@app.get("/")
async def root():
    return {"status": "Dansu Backend V5 - Ultra Logging activado. Revisa los logs en Render."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
