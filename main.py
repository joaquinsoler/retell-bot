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
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

def ensure_calendar_access(calendar_id: str):
    try:
        service = get_calendar_service()
        service.calendarList().insert(body={'id': calendar_id}).execute()
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code != 409:
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
        freebusy = service.freebusy().query(body=body).execute()
        busy = freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy) == 0
    except Exception:
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
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
    return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()

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

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres la voz y el asistente virtual exclusivo de {nombre_negocio}, un negocio enfocado en el sector de {sector}. Tu objetivo principal es atender a los clientes con la máxima amabilidad, empatía y profesionalidad, ofreciendo una conversación fluida, natural y cercana.

**ALCANCE DE TUS FUNCIONES:**
- Solo puedes dar información del negocio y agendar nuevas citas.
- No puedes cancelar, modificar ni gestionar citas existentes. Si te lo piden, responde educadamente que solo puedes agendar nuevas y que contacten con el equipo humano.

**TU PERSONALIDAD:**
- Habla con calidez, frases cortas y claras (ideal para teléfono).
- Sé servicial, educado y profesional.

**DATOS DEL NEGOCIO (usa solo estos):**
- Zona: {zona}
- Horario: {horario}
- Servicios: {servicios}
- Email calendario: {calendar_email}

**FLUJO OBLIGATORIO (sigue este orden exacto):**

1. **Inicio de llamada:**
   "Hola, gracias por llamar a {nombre_negocio}. ¿En qué puedo ayudarte hoy? ¿Necesitas información o prefieres reservar una cita?"

2. **Si pide información:** Dásela y pregunta al final si quiere reservar.

3. **Si quiere reservar cita:**
   - **Primero** pregunta la fecha y hora (recuerda siempre que el año es **2026**).
   - Cuando el cliente dé una fecha/hora concreta, responde: "Entendido. Voy a comprobar si ese horario está disponible..."
   - **Después** pide los datos uno a uno: nombre completo, teléfono y motivo.
   - Cuando tengas los 4 datos claros (fecha/hora + nombre + teléfono + motivo), **NO preguntes confirmación**. Llama **directamente** a la herramienta `book_appointment`.

**Cómo llamar a la herramienta:**
- calendar_email: "{calendar_email}"
- summary: "Cita para [Nombre] - [Motivo]"
- start_time: "2026-07-15T10:00:00+02:00" (formato ISO con año 2026 y +02:00)
- end_time: "2026-07-15T10:30:00+02:00"
- description: datos del cliente (opcional)

Si la herramienta confirma éxito → avisa al cliente. Si está ocupado → di que ese horario ya no está disponible y ofrece otra opción.

**REGLAS IMPORTANTES:**
- Nunca menciones términos técnicos (herramienta, función, error, backend...).
- Si hay problema técnico di solo: "Disculpa la pausa, estoy revisando la agenda..."
- Mantén respuestas cortas y naturales para teléfono.
- Habla siempre en español claro."""

# ==================== CREACIÓN DE BOT ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

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
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
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
            "htmlContent": f"""
                <html><body style="font-family: sans-serif; padding: 30px; background-color: #f8fafc; color: #1e293b;">
                    <div style="max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 16px; border: 1px solid #e2e8f0;">
                        <h2 style="color: #0f172a; margin-top: 0;">¡Hola!</h2>
                        <p>Haz clic en el botón inferior para acceder a tu panel:</p>
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{magic_link}" target="_blank" style="background-color: #0078FF; color: white; padding: 14px 28px; text-decoration: none; border-radius: 12px; font-weight: 600; display: inline-block;">Acceder a mi Panel ✨</a>
                        </div>
                    </div>
                </body></html>
            """
        }
        r = requests.post("https://api.brevo.com/v3/smtp/email", headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"}, json=payload, timeout=15)
        return r.status_code in (200, 201)
    except Exception:
        return False

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email or "@" not in email:
            raise HTTPException(400, "Email inválido")
        token = create_magic_token(email)
        magic_link = f"https://retell-bot.onrender.com/redirect-to-wix?token={token}"
        if send_magic_link_email(email, magic_link):
            return {"status": "success", "message": "Enlace enviado."}
        raise HTTPException(500, "Error enviando email.")
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    email = verify_magic_token(token)
    if not email:
        return "<html><body><h3>❌ Enlace inválido o caducado.</h3></body></html>"
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    SESIONES_ACTIVAS[client_ip] = {"email": email, "expira": datetime.utcnow() + timedelta(minutes=5)}
    return '<html><head><meta http-equiv="refresh" content="0;url=https://www.dansu.info/blank-4"></head><body><h3>Verificación completada. Cargando panel...</h3></body></html>'

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

# ==================== ENDPOINTS RESTANTES (sin cambios) ====================
@app.post("/get-user-bots")
async def get_user_bots(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()
        return {"status": "success", "bots": bots}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        nombre_negocio = data.get("nombre_negocio")
        sector = data.get("sector")
        servicios = data.get("servicios")
        horario = data.get("horario")
        zona = data.get("zona")
        calendar_email = data.get("google_calendar_email")
        asistente_nombre = data.get("asistente")

        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta agent_id")

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if not agent_info or "response_engine" not in agent_info:
            raise HTTPException(status_code=404, detail="Agente no encontrado")

        llm_id = agent_info["response_engine"].get("llm_id")
        nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

        retell_request("PATCH", f"/update-retell-llm/{llm_id}", {
            "general_prompt": nuevo_prompt,
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
                        "description": {"type": "string"}
                    },
                    "required": ["calendar_email", "summary", "start_time", "end_time"]
                }
            }]
        })

        voice_id_tecnico = VOICE_MAPPING.get(asistente_nombre, agent_info.get("voice_id"))
        if voice_id_tecnico:
            retell_request("PATCH", f"/update-agent/{agent_id}", {"voice_id": voice_id_tecnico})

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio=%s, sector=%s, servicios=%s, horario=%s, zona=%s, google_calendar_email=%s, asistente=%s
            WHERE agent_id=%s;
        """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id_tecnico, agent_id))
        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "message": "Asistente actualizado correctamente."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta agent_id")

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if agent_info:
            llm_id = agent_info.get("response_engine", {}).get("llm_id")
            numbers_res = retell_request("GET", "/v2/list-phone-numbers")
            if numbers_res and "items" in numbers_res:
                for phone in numbers_res["items"]:
                    if any(a.get("agent_id") == agent_id for a in phone.get("inbound_agents", [])):
                        retell_request("PATCH", f"/update-phone-number/{phone['phone_number']}", {"inbound_agents": []})
            retell_request("DELETE", f"/delete-agent/{agent_id}")
            if llm_id:
                retell_request("DELETE", f"/delete-retell-llm/{llm_id}")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "success", "message": "Asistente eliminado correctamente."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args", data)
        create_google_event(
            args.get("calendar_email"),
            args.get("summary"),
            args.get("start_time"),
            args.get("end_time"),
            args.get("description", "")
        )
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}
    except Exception as e:
        return {"code": "ERROR", "message": str(e)}

@app.post("/verify-calendar-access")
@app.post("/verify-calendar-access/")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        create_google_event(
            calendar_email,
            "🧪 Prueba de conexión - Dansu",
            "2026-07-01T10:00:00+02:00",
            "2026-07-01T10:30:00+02:00",
            bypass_availability=True
        )
        return {"status": "success", "message": "Acceso verificado"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        return create_bot_for_client(
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"status": "Dansu Backend OK"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
