import os
import json
import uuid
import smtplib
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

app = FastAPI(title="Dansu Backend Completo con Enlaces Mágicos y Google Calendar")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")

# Nuevas variables para la autenticación segura por Brevo
BREVO_SMTP_USER = os.getenv("BREVO_SMTP_USER")
BREVO_SMTP_PASSWORD = os.getenv("BREVO_SMTP_PASSWORD")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://tusitio.wixsite.com/dansu-dashboard")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas de Retell, Google o Base de Datos.")

if not BREVO_SMTP_USER or not BREVO_SMTP_PASSWORD:
    raise Exception("Faltan las variables de entorno de Brevo (BREVO_SMTP_USER o BREVO_SMTP_PASSWORD).")

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
    print("✅ Base de datos PostgreSQL inicializada con éxito (Tablas: asistentes y magic_tokens).")

init_db()

# ==================== SERVICIO DE ENVÍO BREVO (SMTP) ====================
def enviar_correo_brevo(destinatario: str, enlace_magico: str):
    """Envía el email del enlace mágico utilizando el servidor SMTP Relay de Brevo"""
    try:
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

        # Envío vía Relay SMTP de Brevo (Puerto 587 con TLS mas autenticación)
        server = smtplib.SMTP('smtp-relay.brevo.com', 587)
        server.starttls()
        server.login(BREVO_SMTP_USER, BREVO_SMTP_PASSWORD)
        server.sendmail(BREVO_SMTP_USER, destinatario, msg.as_string())
        server.quit()
        print(f"📧 Enlace mágico enviado con éxito a {destinatario}")
        return True
    except Exception as e:
        print(f"❌ Error al enviar el correo con Brevo SMTP: {e}")
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
        print(f"❌ Error al autenticar con Google Calendar: {e}")
        raise e

def create_google_event(calendar_id, summary, start_iso, end_iso, bypass_availability=False):
    """Inserta o valida un evento en la cuenta de Google Calendar del cliente"""
    service = get_calendar_service()
    
    if not bypass_availability:
        # Lógica por defecto para comprobar disponibilidad en la franja horaria
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
    """Crea un agente conversacional nativo en Retell AI inyectándole su prompt adaptado"""
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
        "llm_websocket_url": "wss://api.retellai.com/llm-websocket",
        "voice_settings": {
            "speed": 1.0,
            "temperature": 0.5
        },
        "system_prompt": prompt_base
    }
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code != 201:
        raise Exception(f"Error en la API de Retell AI: {response.text}")
    return response.json()

# ==================== ENDPOINTS DE ENLACES MÁGICOS (NUEVO) ====================

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    """Genera el token temporal único y dispara el correo por Brevo si el usuario existe"""
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        
        if not email:
            raise HTTPException(status_code=400, detail="El email es obligatorio")

        # Verificar en PostgreSQL si tiene algún bot configurado antes de mandar el email
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM asistentes WHERE google_calendar_email = %s LIMIT 1;", (email,))
        user_exists = cur.fetchone()
        
        if not user_exists:
            cur.close()
            conn.close()
            # Por seguridad (prevención de enumeración de cuentas), indicamos éxito simulado
            return {"status": "success", "message": "Si tu correo electrónico está registrado, recibirás un enlace mágico de acceso en unos instantes."}

        # Crear token seguro de un solo uso válido por 15 minutos
        token = str(uuid.uuid4())
        expiracion = datetime.utcnow() + timedelta(minutes=15)

        cur.execute(
            "INSERT INTO magic_tokens (email, token, expiracion) VALUES (%s, %s, %s);",
            (email, token, expiracion)
        )
        conn.commit()
        cur.close()
        conn.close()

        # Montar la URL final a la que viajará el usuario en Wix
        enlace_magico = f"{FRONTEND_URL}?token={token}"
        
        # Procesar el envío mediante Brevo
        envio_ok = enviar_correo_brevo(email, enlace_magico)
        if not envio_ok:
            raise HTTPException(status_code=500, detail="Error interno del sistema al intentar enviar el correo vía Brevo.")

        return {"status": "success", "message": "Enlace de acceso enviado correctamente. Por favor, revisa tu bandeja de entrada."}
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Error en request-magic-link: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-magic-token")
async def verify_magic_token(request: Request):
    """Valida el token de la URL, lo inhabilita inmediatamente y devuelve el listado de bots"""
    try:
        data = await request.json()
        token = data.get("token", "").strip()

        if not token:
            raise HTTPException(status_code=400, detail="Token no suministrado")

        conn = get_db_connection()
        cur = conn.cursor()
        
        # Validar si el token existe, no ha caducado y no ha sido utilizado previamente
        cur.execute(
            "SELECT email, expiracion FROM magic_tokens WHERE token = %s AND utilizado = FALSE LIMIT 1;",
            (token,)
        )
        token_record = cur.fetchone()

        if not token_record:
            cur.close()
            conn.close()
            raise HTTPException(status_code=401, detail="El enlace mágico no es válido o ya ha sido utilizado con anterioridad.")

        # Control exacto de expiración temporal basada en UTC
        if datetime.utcnow() > token_record["expiracion"]:
            cur.close()
            conn.close()
            raise HTTPException(status_code=401, detail="El enlace mágico ha expirado (el tiempo máximo de validez es de 15 minutos).")

        email_usuario = token_record["email"]

        # Inhabilitar el token (Single-use token)
        cur.execute("UPDATE magic_tokens SET utilizado = TRUE WHERE token = %s;", (token,))
        
        # Extraer los asistentes de este cliente autenticado
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email_usuario,))
        bots = cur.fetchall()
        
        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "email": email_usuario, "bots": bots}
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Error en verify-magic-token: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== RESTO DE ENDPOINTS DE NEGOCIO ORIGINALES ====================

@app.post("/verify-calendar-access")
@app.post("/verify-calendar-access/")
async def verify_calendar_access(request: Request):
    """Endpoint que invoca Wix para comprobar la conexión inicial con el calendario"""
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
    """Crea y persiste un nuevo bot tras procesarse la suscripción"""
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
        
        # 2. Guardar en la Base de Datos PostgreSQL
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), data.get("google_calendar_email"),
            data.get("asistente"), agent_id, "+34900000000"  # Número de prueba por defecto
        ))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return {"status": "success", "agent_id": agent_id, "message": "Asistente virtual creado e indexado correctamente."}
    except Exception as e:
        print(f"❌ Error en create-retell-bot: {e}")
        return {"code": "ERROR", "message": str(e)}


@app.post("/update-retell-bot")
async def update_retell_bot(request: Request):
    """Actualiza la configuración de un bot existente en BD y actualiza su prompt/voz en Retell AI"""
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el parámetro 'agent_id' obligatorio.")

        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        
        # 1. Actualizar Datos del Agente en la API de Retell
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
            "system_prompt": nuevo_prompt
        }
        
        retell_res = requests.patch(url, json=payload, headers=headers)
        if retell_res.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Error actualizando en Retell: {retell_res.text}")

        # 2. Actualizar registros locales en PostgreSQL
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
        print(f"❌ Error en update-retell-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete-retell-bot")
async def delete_retell_bot(request: Request):
    """Elimina permanentemente el bot de Retell AI y de la base de datos local"""
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el parámetro 'agent_id'.")

        # 1. Dar de baja en Retell AI
        url = f"https://api.retellai.com/delete-agent/{agent_id}"
        headers = {"Authorization": f"Bearer {RETELL_API_KEY}"}
        
        retell_res = requests.delete(url, headers=headers)
        # Nota: Si el bot ya fue borrado en Retell, permitimos continuar para limpiar la base de datos local
        if retell_res.status_code not in [200, 204, 404]:
            raise HTTPException(status_code=500, detail=f"Error al eliminar en Retell: {retell_res.text}")

        # 2. Eliminar de PostgreSQL
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
        print(f"❌ Error en delete-retell-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Para ejecución en desarrollo local
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
