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

app = FastAPI(title="Dansu Backend Completo con Magic Link")

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
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            preguntas_agenda TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Base de datos PostgreSQL inicializada y lista.")

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
        busy_periods = freebusy_query.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy_periods) == 0
    except Exception as e:
        print(f"⚠️ Error FreeBusy: {e}")
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
            'description': (description or "Cita agendada por Dansu AI"),
            'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        created = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
        return created
    except Exception as e:
        print(f"❌ Error Google Calendar: {e}")
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
        print(f"❌ Error Retell: {e}")
        return None

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, preguntas_agenda=""):
    additional_section = ""
    if preguntas_agenda and preguntas_agenda.strip():
        additional_section = f"""

**PREGUNTAS ADICIONALES OBLIGATORIAS DEL NEGOCIO (MUY IMPORTANTE - NO LAS OMITAS NUNCA):**
El negocio ha definido estas preguntas específicas que **debes hacer obligatoriamente** a todo cliente que desee agendar una cita. Hazlas de forma natural, educada y conversacional, **una por una**, siempre **después** de haber recogido los 4 datos básicos de la cita (día/hora, nombre completo, teléfono y motivo):

{preguntas_agenda}

Cuando vayas a llamar a la herramienta `book_appointment`, incluye **todas las respuestas** del cliente (tanto las básicas como estas preguntas adicionales) de forma clara, estructurada y legible dentro del campo `description`."""

    return f"""Eres la voz y el asistente virtual exclusivo de {nombre_negocio}, un negocio enfocado en el sector de {sector}. Tu objetivo principal es atender a los clientes con la máxima amabilidad, empatía y profesionalidad, ofreciendo una conversación fluida, natural y cercana.

**ALCANCE DE TUS FUNCIONES (Muy Importante):**
- Tus únicas capacidades y tareas autorizadas son: **dar información detallada sobre el negocio** y **agendar nuevas citas**.
- Si el usuario te solicita cancelar una cita, eliminar una reserva existente, modificar un horario ya agendado o realizar cualquier otra gestión administrativa, debes aclararle de forma muy educada que no tienes acceso para realizar esa acción.

**TU PERSONALIDAD Y TONO REQUERIDO:**
- Habla con calidez, usando frases cortas y claras. Escucha activamente y muestra interés real por ayudar.
- Muéstrate siempre servicial, educado, paciente y con un trato comercial impecable.

**INFORMACIÓN OPERATIVA DEL NEGOCIO:**
- Ubicación / Zona de servicio: {zona}
- Horario comercial: {horario}
- Servicios ofrecidos: {servicios}
- Email del Google Calendar institucional: {calendar_email}
{additional_section}

**FLUJO NATURAL PARA RECOGER DATOS Y AGENDAR CITA:**
Avanza de manera conversacional preguntando los datos **uno a uno**:
1. Día y Hora
2. Nombre Completo
3. Número de Teléfono
4. Motivo de la Cita

Solo cuando tengas estos 4 datos + hayas hecho las preguntas adicionales (si existen), llama a `book_appointment`.

**REGLAS CRÍTICAS:**
- NUNCA uses lenguaje técnico en la llamada.
- Si el hueco está ocupado, responde de forma amable y ofrece alternativas.
- Tu prioridad es ofrecer una experiencia excelente de atención al cliente."""

# ==================== CREACIÓN ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email, preguntas_agenda=""):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, preguntas_agenda)

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda la cita en el calendario del negocio.",
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
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number, preguntas_agenda)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id, agent_id, free_number, preguntas_agenda))
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}

# ==================== MAGIC LINK & AUTENTICACIÓN ====================
# (Se mantiene exactamente igual que en tu versión original)

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
            "htmlContent": f"""
                <html>
                <body style="font-family: sans-serif; padding: 30px; background-color: #f8fafc; color: #1e293b;">
                    <div style="max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 16px; border: 1px solid #e2e8f0;">
                        <h2 style="color: #0f172a; margin-top: 0;">¡Hola!</h2>
                        <p>Haz clic en el botón inferior para iniciar sesión de forma segura en tu panel de control:</p>
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{magic_link}" target="_blank" style="background-color: #0078FF; color: white; padding: 14px 28px; text-decoration: none; border-radius: 12px; font-weight: 600; display: inline-block;">Acceder a mi Panel ✨</a>
                        </div>
                    </div>
                </body>
                </html>
            """
        }
        r = requests.post("https://api.brevo.com/v3/smtp/email", headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"}, json=payload, timeout=15)
        return r.status_code in (200, 201)
    except Exception:
        return False

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
    raise HTTPException(500, "Error enviando email.")

@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    email = verify_magic_token(token)
    if not email:
        return "<html><body><h3>❌ Enlace inválido o caducado.</h3></body></html>"
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    SESIONES_ACTIVAS[client_ip] = {"email": email, "expira": datetime.utcnow() + timedelta(minutes=5)}
    wix_url = "https://www.dansu.info/blank-4"
    return f'<html><head><meta http-equiv="refresh" content="0;url={wix_url}"></head><body></body></html>'

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

# ==================== ENDPOINTS DE GESTIÓN ====================
@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    data = await request.json()
    agent_id = data.get("agent_id")
    nombre_negocio = data.get("nombre_negocio")
    sector = data.get("sector")
    servicios = data.get("servicios")
    horario = data.get("horario")
    zona = data.get("zona")
    calendar_email = data.get("google_calendar_email")
    asistente_nombre = data.get("asistente")
    preguntas_agenda = data.get("preguntas_agenda", "")

    if not agent_id:
        raise HTTPException(status_code=400, detail="Falta el agent_id")

    agent_info = retell_request("GET", f"/get-agent/{agent_id}")
    if not agent_info or "response_engine" not in agent_info:
        raise HTTPException(status_code=404, detail="No se encontró el agente")

    llm_id = agent_info["response_engine"].get("llm_id")
    nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, preguntas_agenda)

    llm_update = retell_request("PATCH", f"/update-retell-llm/{llm_id}", {
        "general_prompt": nuevo_prompt,
        "general_tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda la cita en el calendario del negocio.",
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

    if not llm_update:
        raise HTTPException(status_code=500, detail="Error actualizando LLM")

    voice_id_tecnico = VOICE_MAPPING.get(asistente_nombre)
    if voice_id_tecnico:
        retell_request("PATCH", f"/update-agent/{agent_id}", {"voice_id": voice_id_tecnico})

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE asistentes 
        SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, zona = %s, 
            google_calendar_email = %s, asistente = %s, preguntas_agenda = %s
        WHERE agent_id = %s;
    """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id_tecnico, preguntas_agenda, agent_id))
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "success"}

# (El resto de endpoints como delete, book-appointment, verify-calendar-access, etc. se mantienen exactamente igual que en tu versión original)

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    payload = await request.json()
    data = payload if isinstance(payload, dict) else payload.get("data", payload)
    voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
    return create_bot_for_client(
        data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
        data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email"),
        data.get("preguntas_agenda", "")
    )

@app.get("/")
async def root():
    return {"status": "Dansu Backend Completo OK"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
