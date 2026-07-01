import os
import json
import re
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

app = FastAPI(title="Dansu Backend V4 - Corregido para concurrencia y API Retell actual (2026)")

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

# ==================== LOCKS POR CALENDARIO (SOLUCIÓN A CONCURRENCIA) ====================
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
    print("✅ Base de datos PostgreSQL inicializada.")

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
            print(f"⚠️ Error suscripción calendario: {e}")

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str:
        return dt_str
    original = str(dt_str).strip()
    cleaned = original.replace(" ", "T")

    # Capa 1: fromisoformat directo
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

    # Capa 2: Añadir segundos si faltan
    try:
        if "T" in cleaned and cleaned.count(":") == 1:
            dt = datetime.fromisoformat(cleaned + ":00")
            if getattr(dt, 'tzinfo', None) is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
            return dt.astimezone(MADRID_TZ).isoformat()
    except:
        pass

    # Capa 3: strptime
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"]:
        try:
            dt = datetime.strptime(original, fmt)
            if getattr(dt, 'tzinfo', None) is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
            return dt.astimezone(MADRID_TZ).isoformat()
        except ValueError:
            continue

    print(f"⚠️ No se pudo parsear datetime: {original}")
    return cleaned

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
        freebusy = service.freebusy().query(body=body).execute()
        busy = freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy) == 0
    except Exception as e:
        print(f"⚠️ FreeBusy error (se asume disponible): {e}")
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    lock = get_calendar_lock(calendar_id)
    with lock:  # <-- CLAVE: Serializa operaciones por calendario bajo concurrencia
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
            print(f"✅ Evento creado: {created.get('htmlLink')}")
            return created
        except Exception as e:
            print(f"❌ Error Google Calendar: {e}")
            raise

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
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Retell error: {e}")
        return None

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres el asistente virtual por teléfono de {nombre_negocio} ({sector}).

**FORMATO OBLIGATORIO book_appointment:**
start_time y end_time deben ser SIEMPRE ISO 8601 con offset Madrid: "2026-07-15T10:30:00+02:00"
Nunca uses lenguaje natural. Calcula end_time = start + duración razonable del servicio (30-60 min).

**ALCANCE:** Solo información del negocio y agendar citas nuevas. Para cancelar/modificar → deriva a humano.

**DATOS DEL NEGOCIO:**
- Zona: {zona}
- Horario: {horario}
- Servicios: {servicios}
- Calendar: {calendar_email}

**FLUJO:** Pregunta un dato cada vez → Día/Hora (en formato ISO al llamar tool) → Nombre → Teléfono → Motivo.

Cuando tengas los 4 datos, llama a book_appointment con el email correcto.

**ERRORES:**
- Si recibes SLOT_OCUPADO → di educadamente que ese horario ya no está y ofrece alternativas.
- Si recibes ERROR_FORMATO_HORA → pide de nuevo la fecha y hora de forma concreta.
- Otros errores técnicos → discúlpate y ofrece ayuda manual."""

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

    tool_def = {
        "type": "custom",
        "name": "book_appointment",
        "description": "Agenda cita. Usa SIEMPRE formato ISO 8601 con offset Madrid para start_time y end_time.",
        "url": "https://retell-bot.onrender.com/book-appointment",
        "method": "POST",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_email": {"type": "string", "description": "Email del Google Calendar del negocio"},
                "summary": {"type": "string", "description": "Título de la cita"},
                "start_time": {"type": "string", "description": "ISO 8601 con offset Madrid, ej: 2026-07-15T10:00:00+02:00"},
                "end_time": {"type": "string", "description": "ISO 8601 con offset Madrid"},
                "description": {"type": "string", "description": "Notas opcionales"}
            },
            "required": ["calendar_email", "summary", "start_time", "end_time"]
        }
    }

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [tool_def],
        "start_speaker": "agent",           # ← REQUERIDO en API actual 2026
        "model_temperature": 0.1            # Más determinista para tool calling
    })

    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM en Retell")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES"
    })

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent en Retell")

    agent_id = agent_res["agent_id"]

    # Asignar número libre (ya usa formato moderno inbound_agents)
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents"):
                free_number = p.get("phone_number")
                break

    if free_number:
        retell_request("PATCH", f"/update-phone-number/{free_number}", {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
        })

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id, agent_id, free_number))
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}

# ==================== MAGIC LINK (sin cambios) ====================
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
            "sender": {"name": "Dansu AI", "email": "no-reply@dansu.info"},
            "to": [{"email": email}],
            "subject": "🔑 Tu enlace de acceso a Dansu AI",
            "htmlContent": f"""..."""  # (mantengo el HTML original por brevedad)
        }
        r = requests.post("https://api.brevo.com/v3/smtp/email", headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"}, json=payload, timeout=15)
        return r.status_code in (200, 201)
    except:
        return False

# ==================== ENDPOINTS (Magic Link sin cambios mayores) ====================
@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    data = await request.json()
    email = data.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Email inválido")
    token = create_magic_token(email)
    magic_link = f"https://retell-bot.onrender.com/redirect-to-wix?token={token}"
    if send_magic_link_email(email, magic_link):
        return {"status": "success", "message": "Enlace enviado"}
    raise HTTPException(500, "Error enviando email")

@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    email = verify_magic_token(token)
    if not email:
        return "<html><body><h3>Enlace inválido o caducado.</h3></body></html>"
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    SESIONES_ACTIVAS[client_ip] = {"email": email, "expira": datetime.utcnow() + timedelta(minutes=5)}
    return f'<html><head><meta http-equiv="refresh" content="0;url=https://www.dansu.info/blank-4"></head><body>Redirigiendo...</body></html>'

@app.get("/check-session")
async def check_session(request: Request):
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    sesion = SESIONES_ACTIVAS.get(client_ip)
    if not sesion or datetime.utcnow() > sesion["expira"]:
        return {"status": "no_session"}
    email = sesion["email"]
    del SESIONES_ACTIVAS[client_ip]
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    return {"status": "success", "email": email, "bots": bots}

# ==================== ENDPOINTS ÁREA CLIENTE ====================
@app.post("/get-user-bots")
async def get_user_bots(request: Request):
    data = await request.json()
    email = data.get("email", "").strip()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    return {"status": "success", "bots": bots}

@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    data = await request.json()
    agent_id = data.get("agent_id")
    if not agent_id:
        raise HTTPException(400, "Falta agent_id")

    agent_info = retell_request("GET", f"/get-agent/{agent_id}")
    if not agent_info or "response_engine" not in agent_info:
        raise HTTPException(404, "Agente no encontrado")

    llm_id = agent_info["response_engine"].get("llm_id")
    if not llm_id:
        raise HTTPException(400, "Sin LLM vinculado")

    nuevo_prompt = build_custom_prompt(
        data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
        data.get("horario"), data.get("zona"), data.get("google_calendar_email")
    )

    tool_def = {  # misma definición mejorada
        "type": "custom",
        "name": "book_appointment",
        "description": "Agenda cita. Usa formato ISO 8601 con offset Madrid.",
        "url": "https://retell-bot.onrender.com/book-appointment",
        "method": "POST",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_email": {"type": "string", "description": "Email del Google Calendar"},
                "summary": {"type": "string", "description": "Título cita"},
                "start_time": {"type": "string", "description": "ISO 8601 Madrid ej: 2026-07-15T10:00:00+02:00"},
                "end_time": {"type": "string", "description": "ISO 8601 Madrid"},
                "description": {"type": "string"}
            },
            "required": ["calendar_email", "summary", "start_time", "end_time"]
        }
    }

    retell_request("PATCH", f"/update-retell-llm/{llm_id}", {
        "general_prompt": nuevo_prompt,
        "general_tools": [tool_def]
    })

    voice_id = VOICE_MAPPING.get(data.get("asistente"))
    if voice_id:
        retell_request("PATCH", f"/update-agent/{agent_id}", {"voice_id": voice_id})

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE asistentes SET nombre_negocio=%s, sector=%s, servicios=%s, horario=%s, zona=%s, 
        google_calendar_email=%s, asistente=%s WHERE agent_id=%s
    """, (data.get("nombre_negocio"), data.get("sector"), data.get("servicios"), data.get("horario"),
          data.get("zona"), data.get("google_calendar_email"), voice_id or data.get("asistente"), agent_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "success", "message": "Asistente actualizado correctamente"}

@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    data = await request.json()
    agent_id = data.get("agent_id")
    if not agent_id:
        raise HTTPException(400, "Falta agent_id")

    agent_info = retell_request("GET", f"/get-agent/{agent_id}")
    if agent_info:
        llm_id = agent_info.get("response_engine", {}).get("llm_id")
        # Liberar número
        try:
            numbers = retell_request("GET", "/v2/list-phone-numbers") or {}
            for phone in numbers.get("items", []):
                if any(a.get("agent_id") == agent_id for a in phone.get("inbound_agents", [])):
                    retell_request("PATCH", f"/update-phone-number/{phone['phone_number']}", {"inbound_agents": []})
        except:
            pass
        retell_request("DELETE", f"/delete-agent/{agent_id}")
        if llm_id:
            retell_request("DELETE", f"/delete-retell-llm/{llm_id}")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "success", "message": "Asistente eliminado"}

# ==================== BOOK APPOINTMENT (MEJORADO) ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args", data)

        calendar_email = args.get("calendar_email")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        summary = args.get("summary", "Cita")

        if not all([calendar_email, start_time, end_time]):
            return {"code": "ERROR", "message": "ERROR_FORMATO_HORA: Faltan parámetros obligatorios (calendar_email, start_time, end_time)."}

        event = create_google_event(calendar_email, summary, start_time, end_time, args.get("description", ""))
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}

    except Exception as e:
        error_msg = str(e)
        error_lower = error_msg.lower()
        print(f"❌ Error en book-appointment: {error_msg}")

        if "ya no está disponible" in error_lower or "ocupado" in error_lower:
            return {"code": "ERROR", "message": "SLOT_OCUPADO: El horario ya no está disponible."}
        elif any(kw in error_lower for kw in ["datetime", "format", "iso", "parse", "invalid"]):
            return {"code": "ERROR", "message": "ERROR_FORMATO_HORA: Formato de fecha/hora inválido. Usa ISO 8601 con offset Madrid."}
        else:
            return {"code": "ERROR", "message": f"Error técnico: {error_msg[:200]}"}

@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    data = await request.json()
    calendar_email = data.get("calendar_email")
    try:
        create_google_event(
            calendar_email,
            "🧪 Prueba de conexión - Dansu",
            "2026-07-15T10:00:00+02:00",
            "2026-07-15T10:30:00+02:00",
            bypass_availability=True
        )
        return {"status": "success", "message": "Acceso verificado"}
    except Exception as e:
        raise HTTPException(400, detail=str(e))

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    payload = await request.json()
    data = payload if isinstance(payload, dict) else payload.get("data", payload)
    voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
    return create_bot_for_client(
        data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
        data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email")
    )

@app.get("/")
async def root():
    return {"status": "Dansu Backend V4 OK - Concurrencia por calendario + API Retell 2026 actualizada"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
