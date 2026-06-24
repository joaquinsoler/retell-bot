import os
import json
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from jose import JWTError, jwt

app = FastAPI(title="Dansu Backend - Autenticación por Memoria de IP")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY]):
    raise Exception("❌ Faltan variables de entorno críticas para arrancar el servidor.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15

# Almacén temporal de sesiones validadas indexadas por IP (IP: {"email": email, "expira": datetime})
SESIONES_ACTIVAS = {}

# ==================== DB ====================
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

init_db()

# ==================== GOOGLE CALENDAR & RETELL DE RESPALDO ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    return build('calendar', 'v3', credentials=service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES), cache_discovery=False)

def ensure_calendar_access(calendar_id: str):
    try:
        get_calendar_service().calendarList().insert(body={'id': calendar_id}).execute()
    except HttpError as e:
        if e.status_code != 409: print(f"⚠️ Calendar List: {e}")

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str: return dt_str
    dt_str = str(dt_str).strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC")) if dt_str.endswith("Z") else datetime.fromisoformat(dt_str)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=MADRID_TZ)
        return dt.astimezone(MADRID_TZ).isoformat()
    except: return dt_str

def check_availability(calendar_id: str, start_time: str, end_time: str) -> bool:
    try:
        body = {"timeMin": normalize_to_madrid_iso(start_time), "timeMax": normalize_to_madrid_iso(end_time), "timeZone": "Europe/Madrid", "items": [{"id": calendar_id}]}
        fb = get_calendar_service().freebusy().query(body=body).execute()
        return len(fb.get("calendars", {}).get(calendar_id, {}).get("busy", [])) == 0
    except: return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        if not bypass_availability and not check_availability(calendar_id, start_time, end_time): raise Exception("Horario ocupado")
        event = {'summary': summary[:100], 'description': description or "Cita agendada por Dansu AI", 'start': {'dateTime': normalize_to_madrid_iso(start_time), 'timeZone': 'Europe/Madrid'}, 'end': {'dateTime': normalize_to_madrid_iso(end_time), 'timeZone': 'Europe/Madrid'}}
        return get_calendar_service().events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
    except Exception as e: print(f"❌ Google Error: {e}"); raise

VOICE_MAPPING = {
    "Cimo": "11labs-Adrian", "Brynne": "11labs-Brynne", "Chloe": "11labs-Chloe", "Kate": "openai-Nova", "Grace": "openai-Shimmer", 
    "Leland": "11labs-Leland", "Marissa": "11labs-Marissa", "Lily": "11labs-Lily", "Della": "11labs-Delia", "Nico": "openai-Onyx", 
    "Rita": "11labs-Rita", "Meritt": "11labs-Meritt", "Willa": "11labs-Willa", "Maren": "11labs-Maren", "Tasmin": "11labs-Tasmin", 
    "Ashley": "11labs-Ashley", "Andrea": "openai-Alloy", "Claudia": "11labs-Claudia", "Gaby": "11labs-Gaby", "Alejandro": "openai-Echo", "Sloane": "11labs-Sloane"
}

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    return {"status": "success", "agent_id": f"agent_{int(datetime.utcnow().timestamp())}", "phone_number": "+34900000000"}

# ==================== TOKENS & EMAIL ====================
def create_magic_token(email: str):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": email.lower(), "exp": expire}, JWT_SECRET_KEY, algorithm=ALGORITHM)

def verify_magic_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError: return None

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
                        <p>Haz clic en el botón inferior para iniciar sesión de forma segura e inmediata en tu panel de control de asistentes:</p>
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
    except: return False

# ==================== ENDPOINTS DE CONTROL DE ACCESO (ESTRATEGIA REFORZADA IP) ====================

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    print("\n--- 📥 SOLICITUD EN /request-magic-link ---")
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email or "@" not in email: raise HTTPException(400, "Email inválido")

        token = create_magic_token(email)
        magic_link = f"https://retell-bot.onrender.com/redirect-to-wix?token={token}"

        if send_magic_link_email(email, magic_link):
            return {"status": "success", "message": "Enlace enviado de forma transaccional."}
        raise HTTPException(500, "Error enviando email.")
    except Exception as e: raise HTTPException(500, str(e))


@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    """
    PASO CLAVE: El usuario pulsa el enlace en Gmail y llega aquí. 
    Verificamos el token, y en vez de pasárselo a Wix, asociamos su email con su dirección IP actual.
    """
    print(f"\n--- 🔀 PUENTE POR IP ACTIVADO ---")
    email = verify_magic_token(token)
    
    if not email:
        return "<html><body><h3>❌ El enlace es inválido o ha caducado. Por favor, solicita uno nuevo.</h3></body></html>"
    
    # Extraer la IP real del cliente detrás del proxy de Render/Cloudflare
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    
    # Registramos que esta IP tiene autorización para entrar con este email durante 5 minutos
    SESIONES_ACTIVAS[client_ip] = {
        "email": email,
        "expira": datetime.utcnow() + timedelta(minutes=5)
    }
    print(f"✅ IP {client_ip} emparejada temporalmente con {email}")

    # Redirigimos al usuario a Wix completamente limpio de parámetros.
    wix_url = "https://www.dansu.info/blank-4"
    return f"""
    <html>
        <head><meta http-equiv="refresh" content="0;url={wix_url}"></head>
        <body style="font-family:sans-serif; text-align:center; padding-top:50px;">
            <h3>Verificación completada con éxito. Cargando tu panel... 🚀</h3>
        </body>
    </html>
    """


@app.get("/check-session")
async def check_session(request: Request):
    """
    El iFrame de Wix llamará silenciosamente aquí nada más cargar. 
    Comprobamos si su IP se acaba de validar en el correo.
    """
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    print(f"🔍 iFrame de Wix consultando sesión activa para la IP: {client_ip}")
    
    sesion = SESIONES_ACTIVAS.get(client_ip)
    if not sesion:
        return {"status": "no_session"}
    
    if datetime.utcnow() > sesion["expira"]:
        del SESIONES_ACTIVAS[client_ip]
        return {"status": "no_session"}
        
    email = sesion["email"]
    # Consumimos la sesión para que nadie más desde esa IP pueda reutilizarla
    del SESIONES_ACTIVAS[client_ip]
    
    print(f"🎯 ¡Sesión recuperada e inyectada con éxito a Wix para: {email}!")
    
    # Devolvemos de forma directa los bots asignados a este email
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    
    return {"status": "success", "email": email, "bots": bots}

# ==================== RESTO DE ENDPOINTS ORIGINALES MANTENIDOS ====================

@app.post("/verify-magic-token")
async def verify_magic_token_endpoint(request: Request):
    # Lo mantenemos por retrocompatibilidad por si hiciese falta
    data = await request.json()
    email = verify_magic_token(data.get("token"))
    if not email: raise HTTPException(401, "Token caducado")
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    return {"status": "success", "email": email, "bots": bots}

@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    data = await request.json()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE asistentes SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, zona = %s, google_calendar_email = %s, asistente = %s
        WHERE agent_id = %s RETURNING *;
    """, (data.get("nombre_negocio"), data.get("sector"), data.get("servicios"), data.get("horario"), data.get("zona"), data.get("google_calendar_email"), data.get("asistente"), data.get("agent_id")))
    updated_bot = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "success", "bot": updated_bot}

@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    data = await request.json()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM asistentes WHERE agent_id = %s RETURNING *;", (data.get("agent_id"),))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "success"}

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    payload = await request.json()
    data = payload if isinstance(payload, dict) else payload.get("data", payload)
    voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
    bot_res = create_bot_for_client(data.get("nombre_negocio"), data.get("sector"), data.get("servicios"), data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email"))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
    """, (data.get("nombre_negocio"), data.get("sector"), data.get("servicios"), data.get("horario"), data.get("zona"), data.get("google_calendar_email"), data.get("asistente"), bot_res["agent_id"], bot_res["phone_number"]))
    conn.commit()
    cur.close()
    conn.close()
    return bot_res

@app.get("/")
async def root(): return {"status": "✅ Servidor Operativo"}
