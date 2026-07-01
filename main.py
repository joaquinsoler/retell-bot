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

app = FastAPI(title="Dansu Backend V6 - Full Request Logging + Middleware")

# ==================== LOGGING ====================
def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{ts}] [{level}] {msg}")

def log_error(msg: str, exc: Exception = None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if exc:
        print(f"[{ts}] [ERROR] {msg}\n{traceback.format_exc()}")
    else:
        print(f"[{ts}] [ERROR] {msg}")

# ==================== MIDDLEWARE QUE LOGUEA TODO ====================
@app.middleware("http")
async def log_all_requests(request: Request, call_next):
    start = datetime.now()
    
    body_str = ""
    if request.method == "POST":
        try:
            body_bytes = await request.body()
            body_str = body_bytes.decode("utf-8", errors="ignore")[:600]
            request._body = body_bytes  # Permitir que el endpoint vuelva a leerlo
        except Exception as e:
            body_str = f"[Error leyendo body: {str(e)}]"
    
    log(f"[REQUEST] {request.method} {request.url.path} | Headers: {dict(request.headers)} | Body: {body_str}")
    
    response = await call_next(request)
    ms = (datetime.now() - start).total_seconds() * 1000
    log(f"[RESPONSE] {request.method} {request.url.path} → {response.status_code} ({ms:.0f}ms)")
    return response

# ==================== VARIABLES ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    raise Exception("Faltan variables de entorno")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
SESIONES_ACTIVAS = {}
CALENDAR_LOCKS = {}

def get_calendar_lock(email: str) -> Lock:
    if email not in CALENDAR_LOCKS:
        CALENDAR_LOCKS[email] = Lock()
    return CALENDAR_LOCKS[email]

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS asistentes (
        id SERIAL PRIMARY KEY, nombre_negocio VARCHAR(255), sector VARCHAR(255),
        servicios TEXT, horario VARCHAR(255), zona VARCHAR(255),
        google_calendar_email VARCHAR(255), asistente VARCHAR(255),
        agent_id VARCHAR(255) UNIQUE, phone_number VARCHAR(255),
        fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );""")
    conn.commit()
    cur.close()
    conn.close()
    log("DB inicializada")

init_db()

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    creds = creds.with_scopes(SCOPES)
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str: return dt_str
    s = str(dt_str).strip().replace(" ", "T")
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s[:-1]).replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=MADRID_TZ)
        return dt.astimezone(MADRID_TZ).isoformat()
    except:
        pass
    try:
        if "T" in s and s.count(":") == 1:
            dt = datetime.fromisoformat(s + ":00")
            if dt.tzinfo is None: dt = dt.replace(tzinfo=MADRID_TZ)
            return dt.astimezone(MADRID_TZ).isoformat()
    except:
        pass
    for f in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]:
        try:
            dt = datetime.strptime(str(dt_str).strip(), f)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=MADRID_TZ)
            return dt.astimezone(MADRID_TZ).isoformat()
        except:
            continue
    return s

def check_availability(cal_id, start, end):
    try:
        svc = get_calendar_service()
        s = normalize_to_madrid_iso(start)
        e = normalize_to_madrid_iso(end)
        log(f"[GOOGLE] FreeBusy {cal_id} | {s} → {e}")
        fb = svc.freebusy().query(body={"timeMin": s, "timeMax": e, "timeZone": "Europe/Madrid", "items": [{"id": cal_id}]}).execute()
        busy = fb.get("calendars", {}).get(cal_id, {}).get("busy", [])
        if busy:
            log(f"[GOOGLE] Ocupado: {busy}", "WARN")
            return False
        return True
    except Exception as ex:
        log_error(f"[GOOGLE] FreeBusy error {cal_id}", ex)
        return True

def create_google_event(cal_id, summary, start, end, desc="", bypass=False):
    lock = get_calendar_lock(cal_id)
    log(f"[LOCK] Lock para {cal_id}")
    with lock:
        log(f"[LOCK] Lock adquirido {cal_id}")
        try:
            ensure_calendar_access(cal_id)
            s = normalize_to_madrid_iso(start)
            e = normalize_to_madrid_iso(end)
            if not bypass and not check_availability(cal_id, s, e):
                raise Exception("Horario no disponible")
            svc = get_calendar_service()
            ev = {
                "summary": summary[:100],
                "description": desc or "Cita agendada por Dansu AI",
                "start": {"dateTime": s, "timeZone": "Europe/Madrid"},
                "end": {"dateTime": e, "timeZone": "Europe/Madrid"},
                "reminders": {"useDefault": True}
            }
            created = svc.events().insert(calendarId=cal_id, body=ev, sendUpdates="none").execute()
            log(f"[GOOGLE] Evento creado: {created.get('htmlLink')}")
            return created
        except Exception as ex:
            log_error(f"[GOOGLE] Error creando evento {cal_id}", ex)
            raise

def ensure_calendar_access(cal_id):
    try:
        get_calendar_service().calendarList().insert(body={"id": cal_id}).execute()
    except HttpError as e:
        if e.status_code != 409:
            log_error(f"Error calendarList {cal_id}", e)

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

def retell_request(method, endpoint, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        log(f"[RETELL] {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        log_error(f"[RETELL] {endpoint}", e)
        return None

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres el asistente de {nombre_negocio}. Agenda citas usando formato ISO 8601 exacto (ej: 2026-07-15T10:30:00+02:00) en la herramienta book_appointment."""

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    log(f"[CREATE] Creando bot {nombre_negocio}")
    prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)
    tool = {
        "type": "custom", "name": "book_appointment",
        "description": "Agenda cita. Usa formato ISO 8601 con offset Madrid.",
        "url": "https://retell-bot.onrender.com/book-appointment",
        "method": "POST",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_email": {"type": "string"},
                "summary": {"type": "string"},
                "start_time": {"type": "string", "description": "ISO 8601 Madrid"},
                "end_time": {"type": "string", "description": "ISO 8601 Madrid"},
                "description": {"type": "string"}
            },
            "required": ["calendar_email", "summary", "start_time", "end_time"]
        }
    }
    llm = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini", "general_prompt": prompt, "general_tools": [tool],
        "start_speaker": "agent", "model_temperature": 0.1
    })
    if not llm or "llm_id" not in llm: raise Exception("Error LLM")
    agent = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]},
        "voice_id": voice_id, "language": "es-ES"
    })
    if not agent or "agent_id" not in agent: raise Exception("Error Agent")
    aid = agent["agent_id"]
    nums = retell_request("GET", "/v2/list-phone-numbers") or {}
    free = None
    for p in nums.get("items", []):
        if not p.get("inbound_agents"):
            free = p.get("phone_number")
            break
    if free:
        retell_request("PATCH", f"/update-phone-number/{free}", {"inbound_agents": [{"agent_id": aid, "weight": 1.0}]})
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id, aid, free))
    conn.commit()
    cur.close()
    conn.close()
    log(f"[CREATE] Creado {aid} - {free}")
    return {"status": "success", "agent_id": aid, "phone_number": free}

# ==================== ENDPOINTS ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    log("[BOOK] >>>>> ENTRADA AL ENDPOINT <<<<<")
    try:
        raw = (await request.body()).decode("utf-8", errors="ignore")
        log(f"[BOOK] Raw: {raw[:700]}")
        data = json.loads(raw) if raw else {}
        args = data.get("args", data)
        log(f"[BOOK] Args: {args}")

        cal = args.get("calendar_email")
        st = args.get("start_time")
        en = args.get("end_time")
        if not cal or not st or not en:
            return {"code": "ERROR", "message": "Faltan parámetros"}

        create_google_event(cal, args.get("summary", "Cita"), st, en, args.get("description", ""))
        log("[BOOK] Éxito")
        return {"code": "SUCCESS", "message": "Agendado"}
    except Exception as e:
        log_error("[BOOK] Error", e)
        return {"code": "ERROR", "message": str(e)[:250]}

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    payload = await request.json()
    log(f"[CREATE] Payload: {payload}")
    d = payload if isinstance(payload, dict) else payload.get("data", payload)
    vid = VOICE_MAPPING.get(d.get("asistente"), "openai-Alloy")
    return create_bot_for_client(d.get("nombre_negocio"), d.get("sector"), d.get("servicios"),
                                 d.get("horario"), d.get("zona"), vid, d.get("google_calendar_email"))

@app.get("/")
async def root():
    return {"status": "V6 - Middleware logging activo"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
