import os
import json
import uuid
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # Gestión nativa y precisa de zonas horarias en Python 3.9+
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import psycopg2  # Conector nativo de PostgreSQL
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==================== CONFIGURACIÓN DE LOGS ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("DansuBackend")

app = FastAPI(title="Dansu Backend Completo con Enlaces Mágicos y Google Calendar")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")

# Variables para la autenticación segura por Brevo
BREVO_SMTP_USER = os.getenv("BREVO_SMTP_USER")
BREVO_SMTP_PASSWORD = os.getenv("BREVO_SMTP_PASSWORD")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://tusitio.wixsite.com/dansu-dashboard")

# Verificación inicial con logs explícitos
logger.info("Verificando variables de entorno obligatorias...")
if not RETELL_API_KEY:
    logger.error("Falta la variable de entorno: RETELL_API_KEY")
if not GOOGLE_CREDENTIALS_JSON:
    logger.error("Falta la variable de entorno: GOOGLE_CREDENTIALS")
if not DATABASE_URL:
    logger.error("Falta la variable de entorno: DATABASE_URL")
if not BREVO_SMTP_USER:
    logger.error("Falta la variable de entorno: BREVO_SMTP_USER")
if not BREVO_SMTP_PASSWORD:
    logger.error("Falta la variable de entorno: BREVO_SMTP_PASSWORD")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas de Retell, Google o Base de Datos.")

if not BREVO_SMTP_USER or not BREVO_SMTP_PASSWORD:
    raise Exception("Faltan las variables de entorno de Brevo (BREVO_SMTP_USER o BREVO_SMTP_PASSWORD).")

logger.info(f"Variables de entorno cargadas con éxito. FRONTEND_URL configurada en: {FRONTEND_URL}")

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== MAPEO DE VOCES (ELEVENLABS / OPENAI) ====================
VOICE_MAPPING = {
    "Cimo": "11labs-Adrian", "Brynne": "11labs-Brynne", "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova", "Grace": "openai-Shimmer", "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa", "Lily": "11labs-Lily", "Della": "11labs-Delia",
    "Nico": "openai-Onyx", "Rita": "11labs-Rita", "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa", "Maren": "11labs-Maren", "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley", "Andrea": "openai-Alloy", "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby", "Alejandro": "openai-Echo", "Sloane": "11labs-Sloane"
}

# ==================== CONEXIÓN E INICIALIZACIÓN DE POSTGRESQL ====================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    try:
        logger.info("Inicializando conexiones y estructuras de tablas en PostgreSQL...")
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Tabla principal de asistentes
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
        
        # Nueva tabla para los tokens temporales de un solo uso
        cur.execute("""
            CREATE TABLE IF NOT EXISTS magic_tokens (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                token VARCHAR(255) UNIQUE NOT NULL,
                expiracion TIMESTAMP NOT NULL,
                utilizado BOOLEAN DEFAULT FALSE
            );
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Base de datos PostgreSQL inicializada con éxito (Tablas: asistentes y magic_tokens).")
    except Exception as e:
        logger.error(f"❌ Error crítico inicializando la base de datos: {str(e)}", exc_info=True)
        raise e

init_db()

# ==================== SERVICIO DE ENVÍO BREVO (SMTP) ====================
def enviar_correo_brevo(destinatario: str, enlace_magico: str):
    """Envía el email del enlace mágico utilizando el servidor SMTP Relay de Brevo con manejo exhaustivo de errores"""
    logger.info(f"Iniciando intento de envío SMTP vía Brevo hacia: {destinatario}")
    
    try:
        if not BREVO_SMTP_USER or not BREVO_SMTP_PASSWORD:
            logger.error("SMTP abortado: BREVO_SMTP_USER o BREVO_SMTP_PASSWORD están vacíos en la ejecución.")
            return False

        logger.info("Construyendo el mensaje MIME (Multi-part HTML)...")
        msg = MIMEMultipart()
        msg['From'] = BREVO_SMTP_USER
        msg['To'] = destinatario
        msg['Subject'] = "✨ Tu enlace mágico de acceso - Dansu AI"

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
                <h2 style="color: #0078FF; margin-top: 0;">Acceso al Área de Cliente Dansu AI</h2>
                <p>Has solicitado acceder a tu panel de control para gestionar tus asistentes virtuales.</p>
                <p>Haz clic en el siguiente botón para iniciar sesión de forma segura. Este enlace expirará de forma automática en 15 minutos y es de un solo uso:</p>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{enlace_magico}" style="background-color: #0078FF; color: white; padding: 12px 24px; text-decoration: none; font-weight: bold; border-radius: 8px; display: inline-block;">Entrar a mi Panel</a>
                </div>
                <p style="font-size: 12px; color: #666;">Si el botón no funciona, copia y pega este enlace en tu navegador:<br>{enlace_magico}</p>
                <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                <p style="font-size: 12px; color: #94a3b8;">Si no has solicitado este acceso, puedes ignorar este correo de forma totalmente segura.</p>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(html, 'html'))
        logger.info("Mensaje MIME ensamblado correctamente.")

        logger.info("Conectando al host smtp-relay.brevo.com en el puerto 587...")
        server = smtplib.SMTP('smtp-relay.brevo.com', 587, timeout=15)
        server.ehlo()
        server.starttls()
        server.ehlo()
        
        logger.info(f"Intentando autenticación SMTP con el usuario: {BREVO_SMTP_USER}")
        server.login(BREVO_SMTP_USER, BREVO_SMTP_PASSWORD)
        logger.info("Autenticación SMTP de Brevo aceptada de forma correcta.")
        
        logger.info(f"Enviando correo desde {BREVO_SMTP_USER} hacia {destinatario}...")
        server.sendmail(BREVO_SMTP_USER, destinatario, msg.as_string())
        
        server.quit()
        logger.info(f"📧 Enlace mágico enviado con éxito rotundo a {destinatario}")
        return True

    except Exception as e:
        logger.error(f"❌ ERROR EN ENVÍO BREVO SMTP: {str(e)}", exc_info=True)
        return False

# ==================== LÓGICA DE GOOGLE CALENDAR ====================
def get_calendar_service():
    """Inicializa el cliente de Google Calendar usando la cuenta de servicio"""
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scopes = ['https://www.googleapis.com/auth/calendar']
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        logger.error(f"❌ Error al autenticar con Google Calendar: {str(e)}", exc_info=True)
        raise e

def create_google_event(calendar_id, summary, start_iso, end_iso, bypass_availability=False):
    """Inserta o valida un evento en la cuenta de Google Calendar del cliente"""
    logger.info(f"Iniciando flujo de eventos en Google Calendar para: {calendar_id}")
    service = get_calendar_service()
    
    if not bypass_availability:
        logger.info(f"Comprobando disponibilidad (FreeBusy) para el rango: {start_iso} al {end_iso}")
        body = {
            "timeMin": start_iso,
            "timeMax": end_iso,
            "items": [{"id": calendar_id}]
        }
        try:
            free_busy_res = service.freebusy().query(body=body).execute()
            busy_list = free_busy_res.get('calendars', {}).get(calendar_id, {}).get('busy', [])
            if len(busy_list) > 0:
                raise Exception("El hueco seleccionado ya se encuentra ocupado en tu calendario.")
        except HttpError as err:
            if err.resp.status == 404:
                raise Exception(f"No se ha encontrado el calendario '{calendar_id}'. Verifica que la cuenta de servicio tenga acceso.")
            raise err

    event = {
        'summary': summary,
        'start': {'dateTime': start_iso},
        'end': {'dateTime': end_iso},
    }
    return service.events().insert(calendarId=calendar_id, body=event).execute()

# ==================== INTEGRACIÓN DE AGENTES (RETELL AI) ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id):
    """Crea un agente conversacional nativo en Retell AI inyectándole su prompt adaptado (API v2 actual)"""
    logger.info(f"Solicitando creación de agente en Retell AI para el negocio: {nombre_negocio}")
    url = "https://api.retellai.com/create-agent"
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt_base = (
        f"Eres el asistente virtual inteligente de {nombre_negocio}, un negocio del sector {sector}. "
        f"Tus servicios principales son: {servicios}. Tu horario de atención comercial es: {horario}. "
        f"Operas bajo la zona horaria {zona}. Tu objetivo primordial es guiar y atender amablemente a los clientes "
        f"y agendar de forma autónoma sus citas en los huecos disponibles."
    )
    
    payload = {
        "agent_name": f"Bot-{nombre_negocio.replace(' ', '_')}",
        "voice_id": voice_id,
        "response_engine": {
            "type": "custom-llm",
            "llm_websocket_url": "wss://api.retellai.com/llm-websocket"
        },
        "voice_settings": {
            "speed": 1.0,
            "temperature": 0.5
        },
        "system_prompt": prompt_base
    }
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code != 201:
        logger.error(f"Error de respuesta desde los servidores de Retell AI: {response.status_code} - {response.text}")
        raise Exception(f"Error en la API de Retell AI: {response.text}")
        
    logger.info("Agente creado exitosamente en los entornos remotos de Retell AI.")
    return response.json()

# ==================== LÓGICA DE ASIGNACIÓN DINÁMICA DE TELÉFONO LIBRE ====================
def auto_assign_free_phone_number(agent_id: str):
    """Busca en Retell AI el primer número de teléfono disponible (libre) y le asocia este agent_id usando las propiedades correctas de su API"""
    logger.info("Buscando números de teléfono disponibles en la cuenta de Retell AI...")
    url_list = "https://api.retellai.com/list-phone-numbers"
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        res = requests.get(url_list, headers=headers)
        if res.status_code != 200:
            logger.error(f"No se pudo listar los números de teléfono de Retell: {res.text}")
            return None
        
        phone_numbers = res.json()
        numero_libre = None
        
        # Recorrer la lista para encontrar uno que no tenga bound_agent_id ni agent_id asociado
        for phone in phone_numbers:
            if not phone.get("bound_agent_id") and not phone.get("agent_id"):
                numero_libre = phone.get("phone_number")
                logger.info(f"¡Número libre detectado en tu cuenta!: {numero_libre}")
                break
                
        if numero_libre:
            logger.info(f"Vinculando de forma definitiva el número {numero_libre} al Agent ID {agent_id}...")
            url_bind = f"https://api.retellai.com/update-phone-number/{numero_libre}"
            payload = {
                "agent_id": agent_id,
                "bound_agent_id": agent_id
            }
            
            bind_res = requests.patch(url_bind, json=payload, headers=headers)
            if bind_res.status_code == 200:
                logger.info(f"✅ Vínculo exitoso en Retell AI. El asistente ahora responde en el número: {numero_libre}")
                return numero_libre
            else:
                logger.error(f"Error devuelto por Retell al intentar mapear las propiedades del número: {bind_res.text}")
                return None
        else:
            logger.warning("⚠️ No se encontró ningún número de teléfono libre/disponible en tu panel de Retell AI.")
            return None
            
    except Exception as e:
        logger.error(f"Excepción en el proceso de auto-asignación de teléfono: {str(e)}", exc_info=True)
        return None

# ==================== ENDPOINTS DE ENLACES MÁGICOS ====================

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    logger.info("Petición entrante en endpoint POST /request-magic-link")
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        
        if not email:
            raise HTTPException(status_code=400, detail="El email es obligatorio")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM asistentes WHERE google_calendar_email = %s LIMIT 1;", (email,))
        user_exists = cur.fetchone()
        
        if not user_exists:
            cur.close()
            conn.close()
            return {"status": "success", "message": "Si tu correo electrónico está registrado, recibirás un enlace mágico de acceso en unos instantes."}

        token = str(uuid.uuid4())
        expiracion = datetime.utcnow() + timedelta(minutes=15)

        cur.execute(
            "INSERT INTO magic_tokens (email, token, expiracion) VALUES (%s, %s, %s);",
            (email, token, expiracion)
        )
        conn.commit()
        cur.close()
        conn.close()

        enlace_magico = f"{FRONTEND_URL}?token={token}"
        envio_ok = enviar_correo_brevo(email, enlace_magico)
        if not envio_ok:
            raise HTTPException(status_code=500, detail="Error interno del sistema al intentar enviar el correo electrónico vía Brevo.")

        return {"status": "success", "message": "Enlace de acceso enviado correctamente. Por favor, revisa tu bandeja de entrada."}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"❌ Error en request-magic-link: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-magic-token")
async def verify_magic_token(request: Request):
    try:
        data = await request.json()
        token = data.get("token", "").strip()

        if not token:
            raise HTTPException(status_code=400, detail="Token no suministrado")

        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            "SELECT email, expiracion FROM magic_tokens WHERE token = %s AND utilizado = FALSE LIMIT 1;",
            (token,)
        )
        token_record = cur.fetchone()

        if not token_record:
            cur.close()
            conn.close()
            raise HTTPException(status_code=401, detail="El enlace mágico no es válido o ya ha sido utilizado con anterioridad.")

        if datetime.utcnow() > token_record["expiracion"]:
            cur.close()
            conn.close()
            raise HTTPException(status_code=401, detail="El enlace mágico ha expirado.")

        email_usuario = token_record["email"]
        cur.execute("UPDATE magic_tokens SET utilizado = TRUE WHERE token = %s;", (token,))
        
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email_usuario,))
        bots = cur.fetchall()
        
        conn.commit()
        cur.close()
        conn.close()
        
        return {"status": "success", "email": email_usuario, "bots": bots}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"❌ Error en verify-magic-token: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ==================== RESTO DE ENDPOINTS DE NEGOCIO ORIGINALES ====================

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
        return {"status": "success", "message": "Acceso verificado correctamente"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    """Crea el bot en Retell AI, busca y asigna un número libre con el payload correcto y persiste en BD"""
    logger.info("Petición entrante en /create-retell-bot")
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)
        
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        
        # 1. Registrar agente en la API de Retell
        retell_agent = create_bot_for_client(
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), voice_id
        )
        
        agent_id = retell_agent.get("agent_id")
        
        # Llamar a la función para buscar y enlazar dinámicamente un número libre en Retell
        assigned_phone = auto_assign_free_phone_number(agent_id)
        
        # Si no hay ningún número libre en tu cuenta de Retell, usamos el fallback por defecto
        final_phone_number = assigned_phone or "+34900000000"
        
        # 2. Guardar en la Base de Datos PostgreSQL
        logger.info("Guardando registro final del asistente en PostgreSQL...")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), data.get("google_calendar_email"),
            data.get("asistente"), agent_id, final_phone_number
        ))
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Asistente persistido exitosamente con su número libre asignado.")
        
        return {"status": "success", "agent_id": agent_id, "phone_number": final_phone_number, "message": "Asistente virtual creado y número asignado con éxito."}
    except Exception as e:
        logger.error(f"❌ Error crítico en /create-retell-bot: {str(e)}", exc_info=True)
        return {"code": "ERROR", "message": str(e)}


@app.post("/update-retell-bot")
async def update_retell_bot(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el parámetro 'agent_id' obligatorio.")

        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        
        url = f"https://api.retellai.com/update-agent/{agent_id}"
        headers = {
            "Authorization": f"Bearer {RETELL_API_KEY}",
            "Content-Type": "application/json"
        }
        
        nuevo_prompt = (
            f"Eres el asistente virtual inteligente de {data.get('nombre_negocio')}, un negocio del sector {data.get('sector')}. "
            f"Tus servicios principales son: {data.get('servicios')}. Tu horario de atención comercial es: {data.get('horario')}. "
            f"Operas bajo la zona horaria {data.get('zona')}. Tu objetivo primordial es guiar y atender amablemente a los clientes "
            f"y agendar de forma autónoma sus citas en los huecos disponibles."
        )
        
        payload = {
            "voice_id": voice_id,
            "response_engine": {
                "type": "custom-llm",
                "llm_websocket_url": "wss://api.retellai.com/llm-websocket"
            },
            "system_prompt": nuevo_prompt
        }
        
        retell_res = requests.patch(url, json=payload, headers=headers)
        if retell_res.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Error actualizando en Retell: {retell_res.text}")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, zona = %s, asistente = %s
            WHERE agent_id = %s;
        """, (
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), data.get("asistente"), agent_id
        ))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return {"status": "success", "message": "El asistente virtual ha sido actualizado con éxito."}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"❌ Error crítico en /update-retell-bot: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete-retell-bot")
async def delete_retell_bot(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el parámetro 'agent_id'.")

        url = f"https://api.retellai.com/delete-agent/{agent_id}"
        headers = {"Authorization": f"Bearer {RETELL_API_KEY}"}
        
        retell_res = requests.delete(url, headers=headers)
        
        if retell_res.status_code not in [200, 204, 404]:
            raise HTTPException(status_code=500, detail=f"Error al eliminar en Retell: {retell_res.text}")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
        conn.commit()
        cur.close()
        conn.close()
        
        return {"status": "success", "message": "Asistente eliminado de todos los entornos."}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"❌ Error crítico en /delete-retell-bot: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
