import os
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # Gestión nativa y precisa de zonas horarias en Python 3.9+
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests
import psycopg2  # Conector nativo de PostgreSQL
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from jose import JWTError, jwt  # Manejo seguro de tokens del Magic Link

# ==================== CONFIGURACIÓN DE LOGS PARA RENDER ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler()]  # Envía los logs directamente a la consola de Render
)
logger = logging.getLogger("DansuAI-Backend")

app = FastAPI(title="Dansu Backend Completo con Magic Link")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    logger.critical("Faltan variables de entorno críticas en el despliegue.")
    raise Exception("Faltan variables de entorno críticas (RETELL_API_KEY, GOOGLE_CREDENTIALS, DATABASE_URL, JWT_SECRET_KEY o BREVO_API_KEY)")

# Configuración JWT
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15

# Almacén temporal de sesiones validadas indexadas por IP (IP: {"email": email, "expira": datetime})
SESIONES_ACTIVAS = {}

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
    """Crea o actualiza la tabla de asistentes en PostgreSQL al arrancar"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS asistentes (
                id SERIAL PRIMARY KEY,
                nombre_negocio VARCHAR(255),
                sector VARCHAR(255),
                servicios TEXT,
                horario VARCHAR(255),
                duracion_cita INT DEFAULT 30,
                zona VARCHAR(255),
                google_calendar_email VARCHAR(255),
                asistente VARCHAR(255),
                agent_id VARCHAR(255) UNIQUE,
                phone_number VARCHAR(255),
                idioma VARCHAR(50) DEFAULT 'es',
                datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita',
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Migraciones automáticas por si la tabla ya existía sin estas columnas
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS idioma VARCHAR(50) DEFAULT 'es';")
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita';")
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS duracion_cita INT DEFAULT 30;")
        conn.commit()
        logger.info("✅ Base de datos PostgreSQL inicializada, verificada y lista.")
    except Exception as e:
        logger.error(f"❌ Error inicializando la base de datos: {e}", exc_info=True)
    finally:
        cur.close()
        conn.close()

# Inicializamos la estructura de la base de datos al arrancar el backend
init_db()

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")  # Huso horario de referencia absoluto para el negocio

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=SCOPES
    )
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
        logger.info(f"✅ Calendario suscrito: {calendar_id}")
    except HttpError as e:
        if e.status_code == 409:
            logger.info(f"ℹ️ Ya suscrito: {calendar_id}")
        else:
            logger.error(f"⚠️ Error suscripción {e.status_code}: {e}")


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
    dt_madrid = dt.astimezone(MADRID_TZ)
    return dt_madrid.isoformat()


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
        logger.info(f"🔍 Consultando FreeBusy para {calendar_id} entre {iso_start} y {iso_end}")
        freebusy_query = service.freebusy().query(body=body).execute()
        busy_periods = freebusy_query.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        if busy_periods:
            logger.warning(f"❌ Hueco ocupado. Conflictos detectados: {busy_periods}")
            return False
        logger.info("✅ Hueco 100% disponible.")
        return True
    except Exception as e:
        logger.error(f"⚠️ Error al comprobar disponibilidad con FreeBusy: {e}", exc_info=True)
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
        logger.info(f"✅ EVENTO CREADO: {created.get('htmlLink')}")
        return created
    except Exception as e:
        logger.error(f"❌ Error Google Calendar: {e}", exc_info=True)
        raise


# ==================== VOICE MAPPING & RETELL UTILS ====================
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
        logger.info(f"→ Retell {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        logger.error(f"❌ Error de comunicación con Retell: {e}", exc_info=True)
        return None


# ==================== CONSTRUCTOR DEL PROMPT DINÁMICO CORREGIDO ====================
def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma="es", 
                        datos_reserva="Nombre completo, Número de teléfono, Motivo de la cita"):
    idiomas_legibles = {
        "es": "Español de España (es-ES)",
        "en": "Inglés (en-US)",
        "ca": "Catalán (ca-ES)"
    }
    idioma_atencion = idiomas_legibles.get(str(idioma).strip().lower(), "Español de España (es-ES)")

    ahora_madrid = datetime.now(MADRID_TZ)
    
    dias_semana = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"}
    meses_año = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"}
    
    fecha_legible = f"{dias_semana[ahora_madrid.weekday()]}, {ahora_madrid.day} de {meses_año[ahora_madrid.month]} de {ahora_madrid.year}"
    hora_legible = ahora_madrid.strftime("%H:%M")

    return f"""Eres la voz y el asistente virtual exclusivo de {nombre_negocio}, un negocio enfocado en el sector de {sector}.
Tu objetivo principal es atender a los clientes con la máxima amabilidad, empatía y profesionalidad, ofreciendo una conversación fluida, natural y cercana.

**REFERENCIA TEMPORAL OBLIGATORIA (MUY IMPORTANTE):**
- La fecha de hoy es: **{fecha_legible}**.
- La hora actual es: **{hora_legible}** (Zona horaria: Europe/Madrid).
Utiliza esta referencia exacta para interpretar correctamente términos relativos que use el usuario como "hoy", "mañana", "esta tarde", "el próximo lunes" o "ayer", calculando los días en función de este marco.

**CONFIGURACIÓN OBLIGATORIA DE IDIOMA:**
- Debes interactuar, responder, saludar y hablar COMPLETAMENTE en el idioma: **{idioma_atencion}**.
Toda la llamada debe seguir este idioma de forma estricta.

**ALCANCE DE TUS FUNCIONES (Muy Importante):**
- Tus únicas capacidades y tareas autorizadas son: **dar información detallada sobre el negocio** y **agendar nuevas citas**.
- Si el usuario te solicita cancelar una cita, eliminar una reserva existente, modificar un horario ya agendado o realizar cualquier otra gestión administrativa, debes aclararle de forma muy educada que no tienes acceso para realizar esa acción.
Responde con un tono comercial impecable explicando tus límites.

**TU PERSONALIDAD Y TONO REQUERIDO:**
- Habla con calidez, usando frases cortas y claras para mantener la comodidad. Sin embargo, para los números rige la regla de deletreado estricta que figura abajo.
- Escucha activamente. Muéstrate siempre servicial, educado y con un trato comercial impecable.

**INFORMACIÓN OPERATIVA DEL NEGOCIO (Estrictamente real, nunca inventes datos):**
- Ubicación / Zona de servicio: {zona}
- Horario comercial: {horario}
- Servicios ofrecidos: {servicios}
- Email del Google Calendar institucional: {calendar_email}

**FLUJO NATURAL PARA RECOGER DATOS Y AGENDAR CITA:**
Cuando un usuario esté interesado en reservar, avanza de manera conversacional, preguntando los datos uno a uno (nunca todos de golpe en una sola frase):
1. **Día y Hora:** Propón o confirma el momento de la cita según las preferencias del cliente.
2. **Información Requerida del Cliente (OBLIGATORIA):** Para formalizar y confirmar la reserva, debes pedirle de forma obligatoria, educada y uno a uno los siguientes datos estipulados de manera estricta por el negocio: **{datos_reserva}**.
No omitas ninguno. Insiste amablemente si el usuario olvida proveer alguno de ellos.
Solo cuando tengas recopilados la Fecha/Hora y todos los datos requeridos extra listados en (**{datos_reserva}**) de forma exitosa, utiliza la herramienta `book_appointment`.
Debes pasar obligatoriamente el email `{calendar_email}` en el campo `calendar_email`.
En el campo `datos_cliente_recolectados`, debes estructurar la información usando palabras de texto para deletrear obligatoriamente cualquier número recopilado, tal como se especifica en las instrucciones del parámetro de la función.

**REGLAS OBLIGATORIAS DE DICCIÓN Y FORMATEO ALFABÉTICO MASIVO (MÁXIMA PRIORIDAD):**
- Tienes TERMINANTEMENTE PROHIBIDO escribir dígitos numéricos puros (0, 1, 2, 3, 4, 5, 6, 7, 8, 9) o símbolos de hora como el carácter de dos puntos (:) al interactuar con el cliente o al estructurar la llamada. El uso de caracteres numéricos puros satura el canal TTS y provoca lecturas erróneas.
- Cada vez que confirmes, repitas o dictes un NÚMERO DE TELÉFONO, escríbelo en palabras completas separando cada dígito por comas (Ejemplo interno: "seis, uno, uno, dos, dos, tres, tres, cuatro, cuatro"). Las comas forzarán una pronunciación perfecta, pausada y fluida en el motor de voz.
- Cada vez que confirmes o repitas una HORA, escríbela con letras enteras en formato conversacional descriptivo tradicional (Ejemplo interno: "las cuatro y media de la tarde" o "las once en punto de la mañana"). NUNCA uses representaciones en formato militar.

**REGLAS CRÍTICAS DE CONTROL DE ERRORES (Capa de Privacidad de Desarrollo):**
- NUNCA menciones nombres de variables, formatos de código, mensajes de servidores, ni términos técnicos de software en la llamada (como "error de JSON", "función", "endpoint", "404", "backend"). Está estrictamente prohibido.
- Si la herramienta `book_appointment` te devuelve un fallo, un error del sistema o indica que el hueco está ocupado, actúa como un comercial humano resolutivo y amable. Offréce opciones alternativas de inmediato.
"""


# ==================== LÓGICA DE CREACIÓN ====================
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
            "description": "Agenda la cita en el calendario del negocio. Si el hueco está ocupado o falla, devolverá un error.",
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
                    "datos_cliente_recolectados": {
                        "type": "string",
                        "description": "Todos los datos requeridos por el negocio que han sido recolectados. CRÍTICO: Cualquier número de teléfono u hora guardado en esta cadena de texto debe transcribirse OBLIGATORIAMENTE deletreando sus dígitos en palabras legibles separadas por comas (Ejemplo: 'seis, ocho, dos, uno...'). NUNCA uses dígitos numéricos puros aquí."
                    }
                },
                "required": ["calendar_email", "summary", "start_time", "end_time", "datos_cliente_recolectados"]
            }
        }]
    })

    if not llm_res or "llm_id" not in llm_res:
        logger.error("Fallo crítico: No se pudo obtener llm_id al crear el agente en Retell.")
        raise Exception("Error creando LLM")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": lang_retell
    })

    if not agent_res or "agent_id" not in agent_res:
        logger.error("Fallo crítico: No se pudo obtener agent_id al crear el agente en Retell.")
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
    try:
        cur.execute("""
            INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, duracion_cita, zona, google_calendar_email, asistente, agent_id, phone_number, idioma, datos_reserva)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """, (nombre_negocio, sector, servicios, horario, duracion_cita, zona, calendar_email, voice_id, agent_id, free_number, idioma, datos_reserva))
        conn.commit()
        logger.info(f"✅ Bot {agent_id} registrado exitosamente en la base de datos.")
    except Exception as e:
        logger.error(f"❌ Error guardando bot en base de datos: {e}", exc_info=True)
        raise e
    finally:
        cur.close()
        conn.close()

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}


# ==================== UTILS TOKENS & EMAIL (MAGIC LINK) ====================
def create_magic_token(email: str):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": email.lower(), "exp": expire}, JWT_SECRET_KEY, algorithm=ALGORITHM)

def verify_magic_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError as e:
        logger.warning(f"Validación de Token de enlace mágico fallida: {e}")
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
    except Exception as e:
        logger.error(f"Fallo enviando email con Brevo a {email}: {e}", exc_info=True)
        return False


# ==================== ENDPOINTS DE AUTENTICACIÓN ====================
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
            logger.info(f"Magic link solicitado y enviado a: {email}")
            return {"status": "success", "message": "Enlace enviado de forma transaccional."}
        raise HTTPException(500, "Error enviando email.")
    except Exception as e:
        logger.error(f"Error en request-magic-link: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    email = verify_magic_token(token)
    if not email:
        logger.warning("Intento de acceso con Token caducado o corrupto.")
        return "<html><body><h3>❌ El enlace es inválido o ha caducado. Por favor, solicita uno nuevo.</h3></body></html>"
    
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    
    SESIONES_ACTIVAS[client_ip] = {
        "email": email,
        "expira": datetime.utcnow() + timedelta(minutes=5)
    }
    
    wix_url = "https://www.dansu.info/blank-4"
    logger.info(f"Redirección autorizada a Wix para IP {client_ip} vinculada al correo {email}")
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
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    sesion = SESIONES_ACTIVAS.get(client_ip)
    
    if not sesion:
        return {"status": "no_session"}
    
    if datetime.utcnow() > sesion["expira"]:
        del SESIONES_ACTIVAS[client_ip]
        logger.info(f"Sesión expirada por tiempo para la IP: {client_ip}")
        return {"status": "no_session"}
        
    email = sesion["email"]
    del SESIONES_ACTIVAS[client_ip]  # Consumo de un solo uso por seguridad
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        logger.info(f"Sesión consumida correctamente para {email}. Cargados {len(bots)} bots.")
        return {"status": "success", "email": email, "bots": bots}
    except Exception as e:
        logger.error(f"Error en base de datos durante check_session para {email}: {e}", exc_info=True)
        return {"status": "no_session"}
    finally:
        cur.close()
        conn.close()


@app.post("/get-user-bots")
async def get_user_bots(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        return {"status": "success", "bots": bots}
    except Exception as e:
        logger.error(f"Error obtaining bots del usuario: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()


# ==================== ENDPOINT DE ACTUALIZACIÓN (UPDATE) ====================
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
        
        idioma = data.get("idioma", "es")
        datos_reserva = data.get("datos_reserva", data.get("informacion_cita", "Nombre completo, Número de teléfono, Motivo de la cita"))
        
        try:
            duracion_cita = int(data.get("duracion_cita", 30))
        except:
            duracion_cita = 30

        logger.info(f"Actualizando Bot ID: {agent_id} | Idioma: {idioma} | Datos Reserva: {datos_reserva} | Duración: {duracion_cita}")

        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el agent_id")

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if not agent_info or "response_engine" not in agent_info:
            raise HTTPException(status_code=404, detail="No se encontró el agente en Retell AI")

        llm_id = agent_info["response_engine"].get("llm_id")
        if not llm_id:
            raise HTTPException(status_code=400, detail="El agente no dispone de un motor LLM vinculado")

        nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma, datos_reserva)

        llm_update = retell_request("PATCH", f"/update-retell-llm/{llm_id}", {
            "general_prompt": nuevo_prompt,
            "general_tools": [{
                "type": "custom",
                "name": "book_appointment",
                "description": "Agenda la cita en el calendario del negocio. Si el hueco está ocupado o falla, devolverá un error.",
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
                        "datos_cliente_recolectados": {
                            "type": "string",
                            "description": "Todos los datos requeridos por el negocio que han sido recolectados. CRÍTICO: Cualquier número de teléfono u hora guardado en esta cadena de texto debe transcribirse OBLIGATORIAMENTE deletreando sus dígitos en palabras legibles separadas por comas (Ejemplo: 'seis, ocho, dos, uno...'). NUNCA uses dígitos numéricos puros aquí."
                        }
                    },
                    "required": ["calendar_email", "summary", "start_time", "end_time", "datos_cliente_recolectados"]
                }
            }]
        })
        
        if not llm_update:
            raise HTTPException(status_code=500, detail="Error al sincronizar cambios y herramientas con Retell AI")

        voice_id_tecnico = VOICE_MAPPING.get(asistente_nombre)
        
        retell_language_mapping = {"es": "es-ES", "en": "en-US", "ca": "ca-ES"}
        lang_retell = retell_language_mapping.get(str(idioma).strip().lower(), "es-ES")

        agent_patch_data = {"language": lang_retell}
        if voice_id_tecnico:
            agent_patch_data["voice_id"] = voice_id_tecnico
            
        retell_request("PATCH", f"/update-agent/{agent_id}", agent_patch_data)

        if not voice_id_tecnico:
            voice_id_tecnico = agent_info.get("voice_id")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, duracion_cita = %s, zona = %s, google_calendar_email = %s, asistente = %s, idioma = %s, datos_reserva = %s
            WHERE agent_id = %s;
        """, (nombre_negocio, sector, servicios, horario, duracion_cita, zona, calendar_email, voice_id_tecnico, idioma, datos_reserva, agent_id))
        conn.commit()
        
        logger.info(f"✅ Registro persistente actualizado en DB para el bot: {agent_id}")
        return {"status": "success", "message": "Asistente modificado de forma dinámica con éxito."}
    except Exception as e:
        logger.error(f"❌ Error crítico en update-retell-bot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()


@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")

        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el parámetro agent_id")

        logger.info(f"🗑️ Iniciando borrado adaptativo del agente: {agent_id}")
        
        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        
        if agent_info and isinstance(agent_info, dict):
            llm_id = agent_info.get("response_engine", {}).get("llm_id")
            try:
                numbers_res = retell_request("GET", "/v2/list-phone-numbers")
                if numbers_res and "items" in numbers_res:
                    for phone in numbers_res["items"]:
                        agents = phone.get("inbound_agents", [])
                        if any(a.get("agent_id") == agent_id for a in agents):
                            retell_request("PATCH", f"/update-phone-number/{phone['phone_number']}", {
                                "inbound_agents": []
                            })
                            logger.info(f"ℹ️ Número {phone['phone_number']} liberado exitosamente.")
            except Exception as e_phone:
                logger.error(f"⚠️ No se pudo liberar el teléfono del agente {agent_id}: {e_phone}")

            retell_request("DELETE", f"/delete-agent/{agent_id}")
            if llm_id:
                retell_request("DELETE", f"/delete-retell-llm/{llm_id}")
        else:
            logger.warning(f"ℹ️ El agente {agent_id} ya no constaba en Retell. Purgando DB...")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
        conn.commit()
        logger.info(f"✅ Registro limpiado con éxito en PostgreSQL para: {agent_id}")
        return {"status": "success", "message": "Asistente eliminado de forma permanente."}

    except Exception as e:
        logger.error(f"❌ Error crítico en delete-retell-bot: {e}", exc_info=True)
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
            conn.commit()
            return {"status": "success", "message": "Limpieza forzada en base de datos completada tras fallo crítico."}
    
        except Exception as db_err:
            logger.critical(f"Fallo total e irrecuperable en DB: {db_err}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Fallo total e irrecuperable en DB: {str(db_err)}")
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()


# ==================== ENDPOINTS GENERALES ORIGINALES PRESERVADOS ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args", data)

        calendar_email = args.get("calendar_email")
        start_time_str = args.get("start_time")

        duracion_minutos = 30  # Fallback seguro
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT duracion_cita FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC LIMIT 1;", (calendar_email,))
            row = cur.fetchone()
            if row and row.get("duracion_cita"):
                duracion_minutos = int(row["duracion_cita"])
        except Exception as e_db:
            logger.error(f"⚠️ Error al consultar duración de cita en base de datos: {e_db}")
        finally:
            cur.close()
            conn.close()

        try:
            clean_start = str(start_time_str).strip().replace(" ", "T")
            if clean_start.endswith("Z"):
                start_dt = datetime.fromisoformat(clean_start[:-1]).replace(tzinfo=ZoneInfo("UTC"))
            else:
                start_dt = datetime.fromisoformat(clean_start)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=MADRID_TZ)
            
            end_dt = start_dt + timedelta(minutes=duracion_minutos)
            end_time_str = end_dt.isoformat()
        except Exception as e_time:
            logger.error(f"⚠️ Error calculando el tiempo exacto. Usando el de la IA: {e_time}")
            end_time_str = args.get("end_time")  # Fallback al cálculo de la IA si el parseo falla

        datos_cliente = args.get("datos_cliente_recolectados", "")
        
        descripcion_final = "Cita agendada automáticamente por Dansu AI.\n\n"
        if datos_cliente:
            descripcion_final += f"📋 DATOS DEL CLIENTE:\n{datos_cliente}"
        else:
            descripcion_final += args.get("description", "")

        create_google_event(
            calendar_email,
            args.get("summary"),
            start_time_str,
            end_time_str,
            descripcion_final
        )
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}
    except Exception as e:
        logger.error(f"❌ ERROR EN BOOK-APPOINTMENT: {e}", exc_info=True)
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
        return {"status": "success", "message": "Acceso verificado correctamente"}
    except Exception as e:
        logger.error(f"Error en verify-calendar-access: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        
        idioma = data.get("idioma", "es")
        datos_reserva = data.get("informacion_cita", data.get("datos_reserva", "Nombre completo, Número de teléfono, Motivo de la cita"))
        
        try:
            duracion_cita = int(data.get("duracion_cita", 30))
        except:
            duracion_cita = 30

        return create_bot_for_client(
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email"),
            idioma, datos_reserva, duracion_cita
        )
    except Exception as e:
        logger.error(f"Error en create-retell-bot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    return {"status": "Dansu Backend Completo OK"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
