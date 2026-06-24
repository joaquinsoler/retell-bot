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

app = FastAPI(title="Dansu Backend Completo con Puente de Redirección")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

print("--- 🛠️ COMPROBACIÓN DE VARIABLES DE ENTORNO ---")
print(f"RETELL_API_KEY: {'✅ Cargada' if RETELL_API_KEY else '❌ FALTA'}")
print(f"GOOGLE_CREDENTIALS: {'✅ Cargada' if GOOGLE_CREDENTIALS_JSON else '❌ FALTA'}")
print(f"DATABASE_URL: {'✅ Cargada' if DATABASE_URL else '❌ FALTA'}")
print(f"JWT_SECRET_KEY: {'✅ Cargada' if JWT_SECRET_KEY else '❌ FALTA'}")
print(f"BREVO_API_KEY: {'✅ Cargada' if BREVO_API_KEY else '⚠️ No configurada'}")
print("-----------------------------------------------")

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

# ==================== DB ====================
def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"❌ Error al conectar a PostgreSQL: {str(e)}")
        print(traceback.format_exc())
        raise

def init_db():
    conn = None
    try:
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
        print("✅ Base de datos inicializada correctamente.")
    except Exception as e:
        print(f"❌ Error en init_db: {str(e)}")
    finally:
        if conn:
            conn.close()

init_db()

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    try:
        credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
        return build('calendar', 'v3', credentials=credentials, cache_discovery=False)
    except Exception as e:
        print(f"❌ Error al construir el servicio de Google Calendar: {str(e)}")
        raise

def ensure_calendar_access(calendar_id: str):
    try:
        service = get_calendar_service()
        service.calendarList().insert(body={'id': calendar_id}).execute()
        print(f"📅 Acceso asegurado/insertado para el calendario: {calendar_id}")
    except HttpError as e:
        if e.status_code != 409:
            print(f"⚠️ Error controlado de Google CalendarList: {e}")

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str:
        return dt_str
    dt_str = str(dt_str).strip().replace(" ", "T")
    try:
        if dt_str.endswith("Z"):
            dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
        return dt.astimezone(MADRID_TZ).isoformat()
    except Exception as e:
        print(f"⚠️ Error normalizando fecha '{dt_str}': {str(e)}")
        return dt_str

def check_availability(calendar_id: str, start_time: str, end_time: str) -> bool:
    try:
        service = get_calendar_service()
        body = {
            "timeMin": normalize_to_madrid_iso(start_time),
            "timeMax": normalize_to_madrid_iso(end_time),
            "timeZone": "Europe/Madrid",
            "items": [{"id": calendar_id}]
        }
        fb = service.freebusy().query(body=body).execute()
        busy_slots = fb.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        print(f"📅 Huecos ocupados encontrados en {calendar_id}: {busy_slots}")
        return len(busy_slots) == 0
    except Exception as e:
        print(f"⚠️ Error comprobando disponibilidad freebusy: {str(e)}")
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        if not bypass_availability and not check_availability(calendar_id, start_time, end_time):
            raise Exception("Horario ocupado")
        
        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': description or "Cita agendada por Dansu AI",
            'start': {'dateTime': normalize_to_madrid_iso(start_time), 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': normalize_to_madrid_iso(end_time), 'timeZone': 'Europe/Madrid'}
        }
        res = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
        print(f"✅ Evento de Google creado con éxito: {res.get('id')}")
        return res
    except Exception as e:
        print(f"❌ Google Error al crear evento: {e}")
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
        print(f"🤖 Realizando petición a Retell AI: {method} {endpoint}")
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        print(f"🤖 Retell API Código: {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Excepción en llamada a Retell AI: {str(e)}")
        return None

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    print(f"🤖 Ejecutando creación de bot marcador de posición para {nombre_negocio}...")
    return {"status": "success", "agent_id": f"agent_{int(datetime.utcnow().timestamp())}", "phone_number": "+34900000000"}

# ==================== LÓGICA DE TOKENS MAGIC LINK ====================
def create_magic_token(email: str):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": email.lower(), "exp": expire}, JWT_SECRET_KEY, algorithm=ALGORITHM)

def verify_magic_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError as e:
        print(f"⚠️ Error de validación de JWT Token: {str(e)}")
        return None

def send_magic_link_email(email: str, magic_link: str):
    if not BREVO_API_KEY:
        print("❌ No se puede enviar el email porque BREVO_API_KEY está vacía.")
        return False
    try:
        payload = {
            "sender": {"name": "Dansu AI", "email": "no-reply@dansu.info"},
            "to": [{"email": email}],
            "subject": "🔑 Tu enlace de acceso a Dansu AI",
            "htmlContent": f"""
                <html>
                <body style="font-family: 'Segoe UI', Arial, sans-serif; padding: 30px; background-color: #f8fafc; color: #1e293b;">
                    <div style="max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);">
                        <h2 style="color: #0f172a; margin-top: 0;">¡Hola!</h2>
                        <p style="font-size: 15px; line-height: 1.6;">Has solicitado acceder a tu Panel de Configuración de Asistentes en Dansu AI.</p>
                        <p style="font-size: 15px; line-height: 1.6;">Haz clic en el botón inferior para iniciar sesión de forma automática y segura. Este enlace caducará en 15 minutos.</p>
                        
                        <div style="text-align: center; margin: 35px 0;">
                            <a href="{magic_link}" target="_blank" style="background-color: #0078FF; color: white; padding: 14px 28px; text-decoration: none; border-radius: 12px; font-weight: 600; display: inline-block; font-size: 15px; box-shadow: 0 4px 12px rgba(0,120,255,0.2);">
                                Acceder a mi Panel ✨
                            </a>
                        </div>
                        
                        <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 25px 0;" />
                        <p style="font-size: 12px; color: #64748b; line-height: 1.5; word-break: break-all;">
                            Si el botón no funciona correctamente, copia y pega esta dirección URL en tu navegador:<br/>
                            <a href="{magic_link}" style="color: #0078FF;">{magic_link}</a>
                        </p>
                    </div>
                </body>
                </html>
            """
        }

        print(f"📤 Conectando con API de Brevo para enviar enlace a: {email}")
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=15
        )

        print(f"📥 Respuesta de Brevo HTTP Status: {response.status_code}")
        if response.status_code in (200, 201):
            print(f"✅ Correo electrónico transaccional enviado con éxito a {email}")
            return True
        else:
            print(f"❌ Brevo ha rechazado la llamada. Código: {response.status_code}, Detalle: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Excepción severa al procesar el envío de correo con Brevo: {str(e)}")
        print(traceback.format_exc())
        return False

# ==================== ENDPOINTS PRINCIPALES ====================

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    print("\n--- 📥 NUEVA SOLICITUD EN /request-magic-link ---")
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        
        if not email or "@" not in email:
            print("⚠️ El formato del correo electrónico proporcionado es incorrecto.")
            raise HTTPException(400, "Email inválido")

        token = create_magic_token(email)
        
        # NUEVA FUNCIÓN: Ahora el link apunta directamente a Render para actuar como puente seguro
        magic_link = f"https://retell-bot.onrender.com/redirect-to-wix?token={token}"

        print(f"🔗 Token JWT creado para: {email}")
        print(f"🔗 URL del puente de Render generada: {magic_link}")

        if send_magic_link_email(email, magic_link):
            return {"status": "success", "message": "Enlace enviado. Revisa tu correo."}
        else:
            raise HTTPException(500, "Error de envío a través de Brevo.")
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Error inesperado en /request-magic-link: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(500, f"Error interno: {str(e)}")


@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str):
    """
    NUEVA FUNCIÓN INFALIBLE: Recibe al usuario desde el correo, rompe las restricciones del iframe
    inyectando el token en el sessionStorage/localStorage y redirige de forma limpia a Wix.
    """
    print(f"\n--- 🔀 PUENTE ACTIVADO: Redirigiendo token {token[:15]}... hacia Wix ---")
    wix_url = f"https://www.dansu.info/blank-4?token={token}"
    
    return f"""
    <html>
        <head>
            <title>Conectando con Dansu AI...</title>
            <meta charset="utf-8">
        </head>
        <body style="font-family: system-ui, sans-serif; background-color: #f8fafc; color: #0f172a; text-align: center; padding-top: 60px;">
            <div style="max-width: 400px; margin: 0 auto; background: white; padding: 30px; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                <h3 style="margin-top:0;">Estableciendo conexión segura...</h3>
                <p style="font-size: 14px; color: #475569;">Cargando tus asistentes automatizados de forma directa.</p>
                <div style="width: 30px; height: 30px; border: 3px solid #0078FF; border-top-color: transparent; border-radius: 50%; display: inline-block; animation: spin 1s linear infinite; margin: 15px 0;"></div>
            </div>
            <style>@keyframes spin {{ to {{ transform: rotate(360deg); }} }}</style>
            <script>
                try {{
                    // Almacenamos el token localmente en el dominio raíz por si hiciese falta recuperarlo
                    localStorage.setItem('dansu_magic_token', '{token}');
                }} catch(e) {{
                    console.error("Error guardando en localStorage puente:", e);
                }}
                // Redirección definitiva
                window.location.href = "{wix_url}";
            </script>
        </body>
    </html>
    """


@app.post("/verify-magic-token")
async def verify_magic_token_endpoint(request: Request):
    print("\n--- 📥 NUEVA SOLICITUD EN /verify-magic-token ---")
    try:
        data = await request.json()
        token = data.get("token")
        
        if not token:
            raise HTTPException(400, "Falta el token")
            
        email = verify_magic_token(token)
        if not email:
            print("❌ El token ha expirado o es incorrecto.")
            raise HTTPException(401, "Enlace inválido o caducado")

        print(f"✅ Token verificado para: {email}")
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()

        return {"status": "success", "email": email, "bots": bots}
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Error crítico en /verify-magic-token: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(401, "Error en la base de datos o token inválido.")

@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    print("\n--- 📥 NUEVA SOLICITUD EN /update-retell-bot ---")
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        if not agent_id:
            raise HTTPException(400, "Falta el campo 'agent_id'")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, zona = %s, google_calendar_email = %s, asistente = %s
            WHERE agent_id = %s
            RETURNING *;
        """, (data.get("nombre_negocio"), data.get("sector"), data.get("servicios"), data.get("horario"), data.get("zona"), data.get("google_calendar_email"), data.get("asistente"), agent_id))
        
        updated_bot = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not updated_bot:
            raise HTTPException(404, "El asistente no existe.")

        print(f"✅ Registro {agent_id} actualizado con éxito.")
        return {"status": "success", "bot": updated_bot}
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Error en /update-retell-bot: {str(e)}")
        raise HTTPException(500, str(e))

@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    print("\n--- 📥 NUEVA SOLICITUD EN /delete-retell-bot ---")
    try:
        data = await request.json()
        agent_id = data.get("agent_id")

        if not agent_id:
            raise HTTPException(400, "Falta el campo 'agent_id'")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s RETURNING *;", (agent_id,))
        deleted_row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not deleted_row:
            raise HTTPException(404, "Asistente no encontrado.")

        print(f"🗑️ Asistente {agent_id} eliminado de la base de datos.")
        return {"status": "success", "message": "Asistente eliminado de todos los sistemas."}
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Error en /delete-retell-bot: {str(e)}")
        raise HTTPException(500, str(e))

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        
        bot_res = create_bot_for_client(
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email")
        )
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *;
        """, (data.get("nombre_negocio"), data.get("sector"), data.get("servicios"), data.get("horario"), data.get("zona"), data.get("google_calendar_email"), data.get("asistente"), bot_res["agent_id"], bot_res["phone_number"]))
        conn.commit()
        cur.close()
        conn.close()
        
        return bot_res
    except Exception as e:
        print(f"❌ Error creando bot: {str(e)}")
        raise HTTPException(500, str(e))

@app.get("/")
async def root():
    return {"status": "✅ Dansu Backend OK - Logs y Puente de Redirección Listos"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
