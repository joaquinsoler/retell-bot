import os
import json
import random
import string
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Dansu Backend Completo")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")

# API Key de Resend para saltarse el firewall de Render usando HTTP (Puerto 443)
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas (RETELL_API_KEY, GOOGLE_CREDENTIALS o DATABASE_URL)")

# ==================== ALMACENAMIENTO TEMPORAL EN MEMORIA (OTPs) ====================
codigos_verificacion = {}

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== CONEXIÓN E INICIALIZACIÓN DE POSTGRESQL ====================
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
            password VARCHAR(255),
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS password VARCHAR(255);
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Base de datos PostgreSQL verificada y sincronizada.")

init_db()

# ==================== FUNCIONES AUXILIARES ====================
MADRID_TZ = ZoneInfo("Europe/Madrid")

def generar_password_aleatoria(longitud=10):
    caracteres = string.ascii_letters + string.digits
    return ''.join(random.choice(caracteres) for _ in range(longitud))

def enviar_correo_bienvenida(destinatario: str, password: str, es_nuevo: bool, negocio: str):
    """Envía un correo utilizando la API HTTP de Resend saltándose las restricciones de puertos de Render"""
    if not RESEND_API_KEY:
        print("⚠️ Configuración RESEND_API_KEY ausente. Clave generada:", password)
        return False
        
    if es_nuevo:
        texto_password = f"""
        <p>Como es tu primer asistente con nosotros, hemos generado una <strong>Contraseña Maestra de Acceso</strong> única para tu cuenta corporativa:</p>
        <div style="background: #f1f5f9; padding: 15px; text-align: center; font-size: 24px; font-weight: bold; color: #0078FF; border-radius: 12px; margin: 20px 0; border: 1px solid #e2e8f0; letter-spacing: 2px;">
            {password}
        </div>
        <p>Guarda bien esta contraseña. La necesitarás junto a tu correo electrónico para editar, configurar o dar de baja a tus agentes en el futuro.</p>
        """
    else:
        texto_password = f"""
        <p>Hemos vinculado este nuevo agente a tu cuenta existente. Puedes acceder a gestionarlo en tu Área de Cliente utilizando tu <strong>Contraseña Maestra habitual</strong>.</p>
        """

    html_content = f"""
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; color: #333; line-height: 1.6; margin: 0; padding: 0;">
        <div style="max-width: 550px; margin: 40px auto; padding: 30px; border: 1px solid #e2e8f0; border-radius: 16px; background-color: #ffffff; box-shadow: 0 4px 12px rgba(0,0,0,0.03);">
            <h2 style="color: #10B981; margin-top: 0; font-size: 22px;">¡Tu Asistente de Inteligencia Artificial está en Marcha! 🎉</h2>
            <p>Hola,</p>
            <p>El agente virtual inteligente para el negocio <strong>{negocio}</strong> se ha desplegado correctamente en nuestros sistemas telefónicos y de agenda en la nube.</p>
            {texto_password}
            <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 25px 0;">
            <p style="font-size: 12px; color: #94a3b8; margin-bottom: 0;">Soporte oficial de Dansu AI. Si tienes alguna duda técnica, responde directamente a este correo.</p>
        </div>
    </body>
    </html>
    """

    # Llamada HTTPS limpia (Aceptada por Render sin bloqueos)
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": "Dansu AI <onboarding@resend.dev>", # Nota: Cuando verifiques dansutech.com en Resend, podrás poner soporte@dansutech.com
        "to": [destinatario],
        "subject": f"Tu asistente virtual de {negocio} ya está listo - Dansu AI",
        "html": html_content
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        if r.ok:
            print(f"📧 Correo transaccional enviado vía API HTTP con éxito a {destinatario}")
            return True
        else:
            print(f"❌ Fallo en la API de Resend: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        print(f"❌ Error al conectar con la API de Resend: {e}")
        return False

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

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
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        body = {"timeMin": iso_start, "timeMax": iso_end, "timeZone": "Europe/Madrid", "items": [{"id": calendar_id}]}
        freebusy_query = service.freebusy().query(body=body).execute()
        busy_periods = freebusy_query.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return False if busy_periods else True
    except Exception: return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    try:
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        if not bypass_availability and not check_availability(calendar_id, iso_start, iso_end):
            raise Exception("El horario seleccionado ya no está disponible.")
        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': (description or "Cita agendada por Dansu AI"),
            'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'}
        }
        return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
    except Exception as e: raise

# ==================== VOICE MAPPING ====================
VOICE_MAPPING = {
    "Cimo": "11labs-Adrian", "Brynne": "11labs-Brynne", "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova", "Grace": "openai-Shimmer", "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa", "Lily": "11labs-Lily", "Della": "11labs-Delia",
    "Nico": "openai-Onyx", "Rita": "11labs-Rita", "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa", "Maren": "11labs-Maren", "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley", "Andrea": "openai-Alloy", "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby", "Alejandro": "openai-Echo", "Sloane": "11labs-Sloane"
}

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres la voz y el asistente virtual exclusivo de {nombre_negocio}, un negocio enfocado en el sector de {sector}. Tu objetivo principal es atender a los clientes con la máxima amabilidad, empatía y profesionalidad, ofreciendo una conversación fluida, natural y cercana. Tareas: facilitar información sobre el negocio ({servicios}, {horario}, {zona}) y agendar citas llamando a la herramienta `book_appointment` pasándole el email institucional `{calendar_email}`."""

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    email_limpio = calendar_email.strip().lower()
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT password FROM asistentes WHERE google_calendar_email = %s LIMIT 1;", (email_limpio,))
    res_db = cur.fetchone()
    
    es_nuevo = True
    if res_db:
        password_cliente = res_db["password"]
        es_nuevo = False
    else:
        password_cliente = generar_password_aleatoria()

    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, email_limpio)
    
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
                    "end_time": {"type": "string"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time"]
            }
        }]
    })

    if not llm_res or "llm_id" not in llm_res: raise Exception("Error creando LLM en Retell AI")
    
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES"
    })

    if not agent_res or "agent_id" not in agent_res: raise Exception("Error creando Agent en Retell AI")
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

    cur.execute("""
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number, password)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """, (nombre_negocio, sector, servicios, horario, zona, email_limpio, voice_id, agent_id, free_number, password_cliente))
    conn.commit()
    cur.close()
    conn.close()

    enviar_correo_bienvenida(email_limpio, password_cliente, es_nuevo, nombre_negocio)

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number, "es_nuevo": es_nuevo}

# ==================== ENDPOINTS DE VALIDACIÓN ====================

@app.post("/verify-login-password")
async def verify_login_password(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "").strip()

        if not email or not password:
            raise HTTPException(status_code=400, detail="El email y la contraseña son requeridos.")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s AND password = %s ORDER BY id DESC;", (email, password))
        bots = cur.fetchall()
        cur.close()
        conn.close()

        if not bots:
            raise HTTPException(status_code=401, detail="Credenciales incorrectas.")

        return {"status": "success", "bots": bots}
    except HTTPException as he: raise he
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

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
        calendar_email = data.get("google_calendar_email").strip().lower()
        asistente_nombre = data.get("asistente")

        if not agent_id: raise HTTPException(status_code=400, detail="Falta el agent_id")

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if not agent_info: raise HTTPException(status_code=404, detail="No encontrado")
        llm_id = agent_info["response_engine"].get("llm_id")

        nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)
        
        retell_request("PATCH", f"/update-retell-llm/{llm_id}", {
            "general_prompt": nuevo_prompt,
            "general_tools": [{
                "type": "custom",
                "name": "book_appointment",
                "description": "Agenda la cita en el calendario.",
                "url": "https://retell-bot.onrender.com/book-appointment",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "calendar_email": {"type": "string"},
                        "summary": {"type": "string"},
                        "start_time": {"type": "string"},
                        "end_time": {"type": "string"}
                    },
                    "required": ["calendar_email", "summary", "start_time", "end_time"]
                }
            }]
        })

        voice_id_tecnico = VOICE_MAPPING.get(asistente_nombre)
        if voice_id_tecnico:
            retell_request("PATCH", f"/update-agent/{agent_id}", {"voice_id": voice_id_tecnico})
        else:
            voice_id_tecnico = agent_info.get("voice_id")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, zona = %s, google_calendar_email = %s, asistente = %s
            WHERE agent_id = %s;
        """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id_tecnico, agent_id))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "success", "message": "Actualizado"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if agent_info:
            llm_id = agent_info.get("response_engine", {}).get("llm_id")
            retell_request("DELETE", f"/delete-agent/{agent_id}")
            if llm_id: retell_request("DELETE", f"/delete-retell-llm/{llm_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.post("/book-appointment")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        args = json.loads(raw_body).get("args", json.loads(raw_body))
        create_google_event(args.get("calendar_email"), args.get("summary"), args.get("start_time"), args.get("end_time"))
        return {"code": "SUCCESS"}
    except Exception as e: return {"code": "ERROR", "message": str(e)}

@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        create_google_event(data.get("calendar_email"), "🧪 Prueba", "2026-07-01T10:00:00+02:00", "2026-07-01T10:30:00+02:00", bypass_availability=True)
        return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        data = await request.json()
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        return create_bot_for_client(data.get("nombre_negocio"), data.get("sector"), data.get("servicios"), data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email"))
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        return r.json() if r.ok else None
    except Exception: return None

@app.get("/")
async def root(): return {"status": "Dansu Backend Completo OK"}
