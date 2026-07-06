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

app = FastAPI(title="Dansu Backend - Solución Híbrida Final")

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

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS asistentes (
            id SERIAL PRIMARY KEY, nombre_negocio VARCHAR(255), sector VARCHAR(255), servicios TEXT,
            horario VARCHAR(255), duracion_cita INT DEFAULT 30, zona VARCHAR(255),
            google_calendar_email VARCHAR(255), asistente VARCHAR(255), agent_id VARCHAR(255) UNIQUE,
            phone_number VARCHAR(255), idioma VARCHAR(50) DEFAULT 'es',
            datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita',
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS idioma VARCHAR(50) DEFAULT 'es';")
    cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita';")
    cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS duracion_cita INT DEFAULT 30;")
    conn.commit()
    cur.close()
    conn.close()

init_db()

SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials.with_scopes(SCOPES), cache_discovery=False)

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str: return dt_str
    dt_str = str(dt_str).strip().replace(" ", "T")
    if dt_str.endswith("Z"):
        dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC"))
    else:
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=MADRID_TZ)
        except ValueError:
            return dt_str
    return dt.astimezone(MADRID_TZ).isoformat()

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    service = get_calendar_service()
    try:
        service.calendarList().insert(body={'id': calendar_id}).execute()
    except: pass

    iso_start = normalize_to_madrid_iso(start_time)
    iso_end = normalize_to_madrid_iso(end_time)

    if not bypass_availability:
        try:
            body = {"timeMin": iso_start, "timeMax": iso_end, "timeZone": "Europe/Madrid", "items": [{"id": calendar_id}]}
            freebusy = service.freebusy().query(body=body).execute()
            if freebusy.get("calendars", {}).get(calendar_id, {}).get("busy"):
                raise Exception("Horario ocupado")
        except:
            pass

    event = {
        'summary': summary[:100],
        'description': description or "Cita agendada por Dansu AI",
        'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
        'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
        'reminders': {'useDefault': True}
    }
    return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()

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
        logger.error(f"Error Retell: {e}")
        return None

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma="es", 
                        datos_reserva="Nombre completo, Número de teléfono, Motivo de la cita"):
    idiomas_legibles = {"es": "Español de España (es-ES)", "en": "Inglés (en-US)", "ca": "Catalán (ca-ES)"}
    lang = idiomas_legibles.get(str(idioma).strip().lower(), "Español de España (es-ES)")

    ahora = datetime.now(MADRID_TZ)
    dias = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"}
    meses = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"}
    fecha_legible = f"{dias[ahora.weekday()]}, {ahora.day} de {meses[ahora.month]} de {ahora.year}"
    hora_legible = ahora.strftime("%H:%M")

    return f"""Eres el asistente de voz de {nombre_negocio}. Hablas SIEMPRE en {lang}.

**REGLA PRINCIPAL DE PRONUNCIACIÓN DE TELÉFONOS (OBLIGATORIA):**
Cuando tengas que decir un número de teléfono, **SIEMPRE** lo dices en **grupos pequeños de 2 o 3 dígitos** separados por comas. 
Formato correcto y natural:
- "seis uno dos, tres cuatro cinco, seis siete ocho"
- "seis once, veintidós, treinta y tres, cuarenta y cuatro"

**NUNCA** digas el número como un cardinal grande ("seiscientos doce millones...").

**EJEMPLOS CONCRETOS (Few-shot):**

Ejemplo 1:
Cliente dice: Mi teléfono es 612345678
Tú respondes: Entendido. Su teléfono es seis uno dos, tres cuatro cinco, seis siete ocho.

Ejemplo 2 (Confirmación completa antes de agendar):
Tú: Perfecto. Entonces reservamos el lunes 8 de julio a las 10:30 para corte de pelo. Su nombre es Juan Pérez y su teléfono es seis uno dos, tres cuatro cinco, seis siete ocho. ¿Es correcto todo?

Ejemplo 3 (Repetición):
Cliente: ¿Puede repetir mi teléfono?
Tú: Claro, su teléfono es seis uno dos, tres cuatro cinco, seis siete ocho.

**REGLA CRÍTICA JUSTO ANTES DE AGENDAR:**
Justo antes de usar la herramienta `book_appointment`, confirma todos los datos en voz alta. **El teléfono debe decirse siempre en grupos de 2-3 dígitos con comas**, aunque ya lo hayas mencionado antes.

Si estás a punto de decirlo de otra forma, corrígete y usa el formato correcto.

**Tu única función:** Dar información del negocio y agendar citas nuevas.
No puedes cancelar ni modificar citas.

**Datos del negocio:**
- Zona: {zona}
- Horario: {horario}
- Servicios: {servicios}

**Flujo:**
Pide los datos uno a uno ({datos_reserva}). Cuando los tengas todos, confirma en voz alta usando el formato correcto de teléfono en grupos y luego usa la herramienta `book_appointment`.

Recuerda: Cada vez que menciones el teléfono durante la llamada, debe ser en grupos de 2-3 dígitos con comas."""

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
            "description": "Agenda la cita. ANTES de llamar a esta herramienta el asistente DEBE confirmar los datos en voz alta diciendo el teléfono en grupos de 2-3 dígitos con comas (ej: seis uno dos, tres cuatro cinco, seis siete ocho). Esta regla es obligatoria.",
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
        "language": lang_retell
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
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, duracion_cita, zona, google_calendar_email, asistente, agent_id, phone_number, idioma, datos_reserva)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (nombre_negocio, sector, servicios, horario, duracion_cita, zona, calendar_email, voice_id, agent_id, free_number, idioma, datos_reserva))
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}

# ==================== RESTO DE ENDPOINTS (sin cambios estructurales) ====================
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
    payload = {
        "sender": {"name": "Dansu AI", "email": "no-reply@dansu.info"},
        "to": [{"email": email}],
        "subject": "🔑 Tu enlace de acceso a Dansu AI",
        "htmlContent": f"""<html><body style="font-family:sans-serif;padding:30px;background:#f8fafc;">
        <div style="max-width:500px;margin:auto;background:white;padding:30px;border-radius:16px;">
        <h2>¡Hola!</h2><p>Accede a tu panel:</p>
        <a href="{magic_link}" style="background:#0078FF;color:white;padding:14px 28px;text-decoration:none;border-radius:12px;display:inline-block;">Acceder</a>
        </div></body></html>"""
    }
    r = requests.post("https://api.brevo.com/v3/smtp/email", headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"}, json=payload, timeout=15)
    return r.status_code in (200, 201)

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    data = await request.json()
    email = data.get("email", "").strip().lower()
    if not email or "@" not in email: raise HTTPException(400, "Email inválido")
    token = create_magic_token(email)
    magic_link = f"https://retell-bot.onrender.com/redirect-to-wix?token={token}"
    if send_magic_link_email(email, magic_link):
        return {"status": "success"}
    raise HTTPException(500, "Error enviando email")

@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    email = verify_magic_token(token)
    if not email: return "<html><body><h3>Enlace inválido</h3></body></html>"
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    SESIONES_ACTIVAS[client_ip] = {"email": email, "expira": datetime.utcnow() + timedelta(minutes=5)}
    return '<html><head><meta http-equiv="refresh" content="0;url=https://www.dansu.info/blank-4"></head></html>'

@app.get("/check-session")
async def check_session(request: Request):
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    sesion = SESIONES_ACTIVAS.get(client_ip)
    if not sesion or datetime.utcnow() > sesion["expira"]: return {"status": "no_session"}
    email = sesion["email"]
    del SESIONES_ACTIVAS[client_ip]
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    return {"status": "success", "email": email, "bots": bots}

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    payload = await request.json()
    data = payload if isinstance(payload, dict) else payload.get("data", payload)
    voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
    idioma = data.get("idioma", "es")
    datos_reserva = data.get("informacion_cita", data.get("datos_reserva", "Nombre completo, Número de teléfono, Motivo de la cita"))
    try:
        duracion_cita = int(data.get("duracion_cita", 30))
    except:
        duracion_cita = 30
    return create_bot_for_client(data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
        data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email"),
        idioma, datos_reserva, duracion_cita)

@app.post("/book-appointment")
async def book_appointment(request: Request):
    raw = (await request.body()).decode("utf-8")
    data = json.loads(raw) if raw else {}
    args = data.get("args", data)
    calendar_email = args.get("calendar_email")
    start_time_str = args.get("start_time")
    datos_cliente = args.get("datos_cliente_recolectados", "")

    duracion = 30
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT duracion_cita FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC LIMIT 1;", (calendar_email,))
    row = cur.fetchone()
    if row and row.get("duracion_cita"): duracion = int(row["duracion_cita"])
    cur.close()
    conn.close()

    try:
        clean = str(start_time_str).strip().replace(" ", "T")
        if clean.endswith("Z"):
            start_dt = datetime.fromisoformat(clean[:-1]).replace(tzinfo=ZoneInfo("UTC"))
        else:
            start_dt = datetime.fromisoformat(clean)
            if start_dt.tzinfo is None: start_dt = start_dt.replace(tzinfo=MADRID_TZ)
        end_dt = start_dt + timedelta(minutes=duracion)
        end_time_str = end_dt.isoformat()
    except:
        end_time_str = args.get("end_time")

    descripcion = f"Cita agendada por Dansu AI.\n\nDATOS DEL CLIENTE:\n{datos_cliente}"
    create_google_event(calendar_email, args.get("summary"), start_time_str, end_time_str, descripcion)
    return {"code": "SUCCESS", "message": "Cita agendada"}

@app.get("/")
async def root():
    return {"status": "Dansu Backend - Formato en grupos de 2-3 dígitos aplicado"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
