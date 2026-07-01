import os
import json
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

app = FastAPI(title="Dansu Backend - Corregido")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    raise Exception("Faltan variables de entorno críticas")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 20
MADRID_TZ = ZoneInfo("Europe/Madrid")
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
    print("✅ Base de datos lista.")

init_db()

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']

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
            print(f"⚠️ ensure_calendar_access: {e}")

def normalize_to_madrid_iso(dt_str: str) -> str:
    """Parsing robusto que maneja formatos ISO, relativos y con errores"""
    if not dt_str:
        return (datetime.now(MADRID_TZ) + timedelta(hours=2)).isoformat()
    
    original = str(dt_str).strip()
    print(f"[TIME] Input recibido: {original}")
    
    try:
        cleaned = original.replace(" ", "T").rstrip("Z")
        
        if "T" in cleaned:
            dt = datetime.fromisoformat(cleaned)
        else:
            if len(cleaned) == 10:
                dt = datetime.fromisoformat(cleaned + "T12:00:00")
            else:
                dt = datetime.fromisoformat(cleaned)
        
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MADRID_TZ)
        
        result = dt.astimezone(MADRID_TZ).isoformat()
        print(f"[TIME] Normalizado correctamente: {result}")
        return result
    
    except Exception as e:
        print(f"[TIME] Parsing falló para '{original}': {e}")
        now = datetime.now(MADRID_TZ)
        fallback = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
        print(f"[TIME] Usando fallback seguro: {fallback.isoformat()}")
        return fallback.isoformat()

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
        
        if busy:
            print(f"[AVAIL] Ocupado: {busy}")
            return False
        print("[AVAIL] Libre")
        return True
    except Exception as e:
        print(f"[AVAIL] Error FreeBusy: {e}")
        return False

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        
        if not bypass_availability and not check_availability(calendar_id, iso_start, iso_end):
            raise Exception("El horario ya no está disponible o fue reservado recientemente.")

        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': description or "Cita agendada por Dansu AI",
            'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        
        created = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
        print(f"[CALENDAR] Evento creado correctamente: {created.get('htmlLink')}")
        return created
    except HttpError as e:
        if e.status_code == 409:
            raise Exception("Conflicto de horario: ya fue reservado.")
        raise
    except Exception as e:
        print(f"[CALENDAR] Error: {e}")
        raise

# ==================== VOICE MAPPING & RETELL ====================
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
        print(f"[RETELL] Error: {e}")
        return None

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres el asistente virtual de {nombre_negocio} ({sector}).

REGLAS ESTRICTAS:
- Solo das información y agendas citas nuevas.
- Si un horario no está disponible, discúlpate y ofrece 2-3 alternativas cercanas.

INFORMACIÓN DEL NEGOCIO:
- Zona: {zona}
- Horario: {horario}
- Servicios: {servicios}

FLUJO PARA AGENDAR:
1. Confirma día y hora exactos.
2. Pide nombre completo.
3. Pide teléfono.
4. Pide motivo.
5. Llama a book_appointment.

IMPORTANTE: Cuando uses la herramienta book_appointment, SIEMPRE envía start_time y end_time en formato ISO 8601 con zona horaria (ejemplo: 2026-07-05T10:00:00+02:00).

Si la herramienta devuelve error de horario ocupado, responde amablemente ofreciendo alternativas."""

# ==================== CREACIÓN DE BOT ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda cita en el calendario del negocio.",
            "url": "https://retell-bot.onrender.com/book-appointment",
            "method": "POST",
            "parameters": {
                "type": "object",
                "properties": {
                    "calendar_email": {"type": "string"},
                    "summary": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "description": {"type": "string"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time"]
            }
        }]
    })

    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES"
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
            "sender": {"name": "Dansu AI", "email": "no-reply@dansu.info"},
            "to": [{"email": email}],
            "subject": "🔑 Acceso a tu panel de Dansu AI",
            "htmlContent": f"""
                <html><body style="font-family:sans-serif;padding:30px;background:#f8fafc">
                    <div style="max-width:520px;margin:auto;background:white;padding:30px;border-radius:16px">
                        <h2 style="color:#0f172a">¡Hola!</h2>
                        <p>Haz clic para acceder a tu panel de asistentes:</p>
                        <a href="{magic_link}" style="background:#0078FF;color:white;padding:14px 28px;border-radius:12px;text-decoration:none;display:inline-block">Acceder al Panel</a>
                    </div>
                </body></html>
            """
        }
        r = requests.post("https://api.brevo.com/v3/smtp/email", 
                         headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"}, 
                         json=payload, timeout=15)
        return r.status_code in (200, 201)
    except:
        return False

# ==================== ENDPOINTS ====================
@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    data = await request.json()
    email = data.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Email inválido")
    
    token = create_magic_token(email)
    magic_link = f"https://retell-bot.onrender.com/redirect-to-wix?token={token}"
    
    if send_magic_link_email(email, magic_link):
        return {"status": "success"}
    raise HTTPException(500, "Error enviando email")

@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    email = verify_magic_token(token)
    if not email:
        return "<h3>Enlace inválido o caducado.</h3>"
    
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    SESIONES_ACTIVAS[client_ip] = {"email": email, "expira": datetime.utcnow() + timedelta(minutes=20)}
    
    return '<html><head><meta http-equiv="refresh" content="0;url=https://www.dansu.info/blank-4"></head></html>'

@app.get("/check-session")
async def check_session(request: Request):
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    sesion = SESIONES_ACTIVAS.get(client_ip)
    
    if not sesion or datetime.utcnow() > sesion["expira"]:
        SESIONES_ACTIVAS.pop(client_ip, None)
        return {"status": "no_session"}
    
    email = sesion["email"]
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY fecha_creacion DESC;", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    
    return {"status": "success", "email": email, "bots": [dict(b) for b in bots]}

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    payload = await request.json()
    data = payload if isinstance(payload, dict) else payload.get("data", payload)
    voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
    return create_bot_for_client(
        data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
        data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email")
    )

@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args") if isinstance(data.get("args"), dict) else data
        
        calendar_email = args.get("calendar_email")
        summary = args.get("summary")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        description = args.get("description", "")

        if not all([calendar_email, summary, start_time, end_time]):
            raise Exception("Faltan datos obligatorios")

        create_google_event(calendar_email, summary, start_time, end_time, description)
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}
    except Exception as e:
        print(f"[BOOK] Error: {e}")
        return {"code": "ERROR", "message": str(e)}

@app.get("/")
async def root():
    return {"status": "Dansu Backend Corregido - OK"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
