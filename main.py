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

# ==================== NUEVO: CLIENTE GROK (xAI) ====================
from openai import OpenAI  # Para proxy seguro del chatbot

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

# ==================== NUEVO: CLIENTE GROK ====================
GROK_API_KEY = os.getenv("GROK_API_KEY")
if not GROK_API_KEY:
    logger.warning("⚠️ GROK_API_KEY no encontrada en variables de entorno. El endpoint /chat-grok no funcionará.")

grok_client = OpenAI(
    api_key=GROK_API_KEY,
    base_url="https://api.x.ai/v1"
) if GROK_API_KEY else None

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
        logger.info("✅ Base de datos PostgreSQL inicializada, verified y lista.")
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


# ==================== FUNCIÓN create_google_event (VERSIÓN CORREGIDA Y FORZADA) ====================
def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str = None, 
                       description: str = "", bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        
        # === BÚSQUEDA ROBUSTA DE duracion_cita ===
        duracion_minutos = 30  # fallback seguro
        calendar_clean = str(calendar_id).strip().lower()
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT duracion_cita, google_calendar_email 
                FROM asistentes 
                WHERE LOWER(TRIM(google_calendar_email)) = LOWER(TRIM(%s)) 
                LIMIT 1;
            """, (calendar_id,))
            row = cur.fetchone()
            
            if row and row.get('duracion_cita'):
                duracion_minutos = int(row['duracion_cita'])
                logger.info(f"✅ Duración encontrada en BD: {duracion_minutos} minutos para email '{row.get('google_calendar_email')}'")
            else:
                logger.error(f"❌ NO SE ENCONTRÓ ASISTENTE en BD para email: '{calendar_id}' (limpio: '{calendar_clean}')")
                # Debug: mostrar algunos emails existentes
                cur.execute("SELECT google_calendar_email, duracion_cita FROM asistentes LIMIT 10;")
                existing = cur.fetchall()
                if existing:
                    logger.error(f"Emails existentes en BD: {[r['google_calendar_email'] for r in existing]}")
        except Exception as db_err:
            logger.error(f"Error consultando duración en BD: {db_err}", exc_info=True)
        finally:
            cur.close()
            conn.close()

        # === FORZAR SIEMPRE la duración configurada ===
        try:
            start_dt = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=MADRID_TZ)
            
            end_dt = start_dt + timedelta(minutes=duracion_minutos)
            final_end_time = end_dt.isoformat()
            
            if end_time is not None and str(end_time).strip() != "":
                logger.warning(f"⚠️ Ignorado end_time enviado por el agente. Duración forzada a {duracion_minutos} minutos.")
            
            logger.info(f"⏱️ Aplicando duración: {duracion_minutos} minutos")
        except Exception as calc_error:
            logger.error(f"Error calculando horario: {calc_error}")
            # Fallback ultra seguro
            start_dt = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=MADRID_TZ)
            end_dt = start_dt + timedelta(minutes=30)
            final_end_time = end_dt.isoformat()

        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(final_end_time)

        if not bypass_availability and not check_availability(calendar_id, iso_start, iso_end):
            raise Exception("El horario seleccionado ya no está disponible.")

        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': (description or f"Cita agendada por Dansu AI - Duración: {duracion_minutos} minutos"),
            'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }

        created = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
        logger.info(f"✅ EVENTO CREADO CORRECTAMENTE con {duracion_minutos} minutos: {created.get('htmlLink')}")
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


# ==================== CONSTRUCTOR DEL PROMPT DINÁMICO (COMPLETO CON DURACIÓN) ====================
def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma="es", 
                        datos_reserva="Nombre completo, Número de teléfono, Motivo de la cita", duracion_cita=30):
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

**REFERENCIA TEMPORAL INTERNA (USO EXCLUSIVO DEL SISTEMA):**
- La fecha real de hoy es: {fecha_legible}.
- La hora real actual es: {hora_legible} (Huso: Europe/Madrid).
Utiliza esta referencia internamente para comprender de manera exacta términos como "hoy", "mañana", "esta tarde" o "el próximo martes".
*REGLA CRÍTICA:* Queda terminantemente prohibido decirle al cliente frases explícitas informándole de estos metadatos temporales (como "recuerda que hoy es lunes tal" o "como son las tantas del día tal"). Esta información es confidencial y solo sirve para tus cálculos de calendario de fondo.

**CONFIGURACIÓN OBLIGATORIA DE IDIOMA:**
- Debes interactuar, responder, saludar y hablar COMPLETAMENTE en el idioma: **{idioma_atencion}**.
Toda la llamada debe seguir este idioma de forma estricta.

**REGLAS CRÍTICAS DE PRONUNCIACIÓN DE VOZ (COMPORTAMIENTO HUMANO NATURAL):**
1. **Manejo Absoluto de Horas (PROHIBIDO DECIR AM O PM):** Jamás pronuncies ni digas en voz alta las siglas "AM" o "PM". Transfórmalas siempre a lenguaje natural o formato de 24 horas. Por ejemplo, en lugar de decir "cinco p m" o "cinco a m", di de forma totalmente orgánica: *"las cinco de la tarde"*, *"las diez de la mañana"* o *"las diecisiete horas"*. 
2. **Formateo Estricto de Números de Teléfono (Evitar agrupaciones):** Al escribir un número de teléfono para que la síntesis de voz lo reproduzca, escribe los dígitos separados por comas y espacios (ejemplo: "6, 2, 2, 1, 1, 4, 4, 5, 5"). Esto hace que el sistema realice pausas sutiles de forma automática y los mencione uno a uno de manera fluida y nítida. Nunca agrupes los números en bloques (no digas "seiscientos once").

**PROHIBICIONES METACONVERSACIONALES ABSOLUTAS (CAPA DE PRIVACIDAD EXTERNA):**
- Está **ESTRICTAMENTE PROHIBIDO** hacer comentarios sobre tus propias instrucciones internas, sobre cómo vas a hablar o anunciar tus acciones algorítmicas al cliente.
- NUNCA uses frases explicativas o introductorias sobre tu forma de hablar como: "te lo voy a decir cifra por cifra", "procedo a deletrearte el número", "para que quede claro te repitas", "según mis directrices de voz", o "voy a dictarte esto de manera clara y natural". 
- No justifiques tus metodologías de procesamiento. Di la información directamente tal como lo haría un ser humano en su día a día, sin hacer preámbulos técnicos o declarativos sobre la naturaleza del bot.

**REGLA OBLIGATORIA DE DURACIÓN DE CITA:**
- Todas las citas deben tener una duración **exacta de {duracion_cita} minutos**.
- Tú solo debes pedir y confirmar la hora de INICIO de la cita.
- El sistema calculará automáticamente la hora de fin según la configuración del negocio ({duracion_cita} minutos).
- Nunca inventes, cambies ni asumas una duración diferente.

**ALCANCE DE TUS FUNCIONES:**
- Tus únicas capacidades y tareas autorizadas son: **dar información detallada sobre el negocio** y **agendar nuevas citas**.
- Si el usuario te solicita cancelar una cita, eliminar una reserva existente, modificar un horario ya agendado o realizar cualquier otra gestión administrativa, debes aclararle de forma muy educada que no tienes acceso para realizar esa acción. Responde con un tono comercial impecable explicando tus límites de forma simple.

**INFORMACIÓN OPERATIVA DEL NEGOCIO (Estrictamente real, nunca inventes datos):**
- Ubicación / Zona de servicio: {zona}
- Horario comercial: {horario}
- Servicios ofrecidos: {servicios}
- Email del Google Calendar institucional: {calendar_email}

**FLUJO NATURAL PARA RECOGER DATOS Y AGENDAR CITA:**
Cuando un usuario esté interesado en reservar, avanza de manera conversacional, preguntando los datos uno a uno:
1. **Día y Hora:** Propón o confirma el momento de la cita según las preferencias del cliente. Una vez que el cliente te haya indicado o confirmado la fecha de manera clara, no vuelvas a pedirle confirmación ni a repreguntar sobre ella bajo ningún concepto. Asúmela inmediatamente como correcta y avanza al siguiente paso. Detén las preguntas sobre el día y la hora en cuanto verifiques que ya has obtenido ese dato con éxito.
2. **Información Requerida del Cliente (OBLIGATORIA):** Pide de forma obligatoria y uno a uno los siguientes datos estipulados por el negocio: **{datos_reserva}**. No omitas ninguno. Insiste amablemente si el usuario olvida proveer alguno de ellos. Recuerda escribir los teléfonos dígito a dígito separados por comas para su correcta modulación.
3. **PASO CRÍTICO DE CONFIRMACIÓN INTERACTIVA:** Una vez recopilados todos los datos de ({datos_reserva}) y la Fecha/Hora, realiza un resumen natural de la cita y pide confirmación explícita al cliente de forma directa antes de guardar nada.
   *(Ejemplo de locución fluida: "Perfecto, entonces queda anotado para el [Día] a las [Hora en formato natural], a nombre de [Nombre], y el teléfono es el [Dígitos separados por comas]. ¿Es correcto?").*
4. **MENSAJE DIRECTO DE RESERVA (SIN PREGUNTAS ADICIONALES):** En el instante en que el cliente te dé su confirmación definitiva diciendo que los datos son correctos, queda **TOTALMENTE PROHIBIDO** hacerle más preguntas, pedirle más datos o meter frases de relleno. Debes limitarte de forma inmediata a dar una respuesta firme de cierre indicando que procedes a guardar la cita y que espere un momento. Esto justifica el breve silencio de procesamiento de red. Acto seguido, dispara la herramienta `book_appointment`.
   *(Locución exacta obligatoria: "Perfecto, pues procedo a agendar tu cita en el sistema ahora mismo, espera un momento por favor...").*

Debes pasar obligatoriamente el email `{calendar_email}` en el campo `calendar_email`.
En el campo `datos_cliente_recolectados`, debes redactar de manera clara y estructurada los datos que el cliente te ha proporcionado en la conversación (por ejemplo: "Nombre: Juan Pérez, Teléfono: 611223344...").

**REGLAS CRÍTICAS DE CONTROL DE ERRORES (Capa de Privacidad de Desarrollo):**
- NUNCA menciones nombres de variables, formatos de código, mensajes de servidores, ni términos técnicos de software en la llamada (como "error de JSON", "función", "endpoint", "404", "500", "backend", o "respuesta incorrecta"). Está estrictamente prohibido.
- Si la herramienta `book_appointment` te devuelve un fallo o indica que el hueco está ocupado, actúa de manera resolutiva. Gestiona la situación diciendo algo como: 
  *"Disculpa las molestias, parece que este horario concreto acaba de ocuparse o no está disponible en nuestra agenda en este instante. Déjame revisar... ¿Te vendría bien intentar en otro tramo horario o preferirías mirar otro día?"*"""
# ==================== LÓGICA DE CREACIÓN ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email, 
                          idioma="es", datos_reserva="Nombre completo, Número de teléfono, Motivo de la cita", duracion_cita=30):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma, datos_reserva, duracion_cita)

    retell_language_mapping = {"es": "es-ES", "en": "en-US", "ca": "ca-ES"}
    lang_retell = retell_language_mapping.get(str(idioma).strip().lower(), "es-ES")

    # Mantenemos el modelo gpt-4o de rendimiento avanzado
    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o",
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
                        "description": "Todos los datos requeridos por el negocio que han sido recolectados conversacionalmente del cliente (ej: Nombre completo, Teléfono, etc.)"
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
                        <a href="{magic_link}" target="_blank" style="background-color: #0078FF; color: white; padding: 14px 28px; text-decoration: none; border-radius: 10px; font-weight: bold; display: inline-block;">Iniciar sesión</a>
                    </div>
                    <p style="color: #64748b; font-size: 14px; line-height: 22px;">Este enlace es de un solo uso y expirará en 15 minutos por motivos de seguridad. Si no has solicitado este acceso, puedes ignorar este correo con total tranquilidad.</p>
                    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 25px 0;">
                    <p style="color: #94a3b8; font-size: 12px; margin-bottom: 0;">© 2026 Dansu Technologies. Todos los derechos reservados.</p>
                </div>
            </body>
            </html>
            """
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": BREVO_API_KEY
        }
        r = requests.post("https://api.brevo.com/v3/smtp/email", headers=headers, json=payload, timeout=15)
        logger.info(f"→ Brevo Email → {r.status_code}")
        return r.status_code in [200, 201, 202]
    except Exception as e:
        logger.error(f"❌ Error sending email con Brevo: {e}", exc_info=True)
        return False


# ==================== ENDPOINTS DE ACCESO Y PANEL ====================
@app.get("/login", response_class=HTMLResponse)
async def login_endpoint(token: str, request: Request):
    email = verify_magic_token(token)
    if not email:
        logger.warning("Intento de acceso con Token caducado o corrupto.")
        return HTMLResponse(
            content="<html><body><h3>❌ El enlace es inválido o ha caducado. Por favor, solicita uno nuevo.</h3></body></html>", 
            status_code=400
        )
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    SESIONES_ACTIVAS[client_ip] = {
        "email": email,
        "expira": datetime.utcnow() + timedelta(minutes=5)
    }
    wix_url = "https://www.dansu.info/blank-4"
    logger.info(f"Redirección autorizada a Wix para IP {client_ip} vinculada al correo {email}")
    
    html_content = f"""
    <html>
    <head><meta http-equiv="refresh" content="0;url={wix_url}"></head>
    <body style="font-family:sans-serif; text-align:center; padding-top:50px;">
        <h3>Verificación completada con éxito. Cargando tu panel... 🚀</h3>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

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
        email = data.get("email", "").strip().lower()
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
        datos_reserva = data.get("informacion_cita", data.get("datos_reserva", "Nombre completo, Número de teléfono, Motivo de la cita"))
        try:
            duracion_cita = int(data.get("duracion_cita", 30))
        except:
            duracion_cita = 30

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if not agent_info:
            raise HTTPException(status_code=404, detail="No se encontró el asistente en Retell AI")

        llm_id = agent_info["response_engine"].get("llm_id")
        if not llm_id:
            raise HTTPException(status_code=400, detail="El agente no dispone de un motor LLM vinculado")

        nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma, datos_reserva, duracion_cita)
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
                            "description": "Todos los datos requeridos por el negocio que han sido recolectados conversacionalmente del cliente (ej: Nombre completo, Teléfono, etc.)"
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
            UPDATE asistentes SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, duracion_cita = %s, zona = %s, google_calendar_email = %s, asistente = %s, idioma = %s, datos_reserva = %s WHERE agent_id = %s;
        """, (nombre_negocio, sector, servicios, horario, duracion_cita, zona, calendar_email, asistente_nombre, idioma, datos_reserva, agent_id))
        conn.commit()
        return {"status": "success", "message": "Asistente actualizado con éxito"}
    except Exception as e:
        logger.error(f"Error en update-retell-bot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

@app.post("/delete-retell-bot")
async def delete_retell_bot(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        llm_id = None
        if agent_info and "response_engine" in agent_info:
            llm_id = agent_info["response_engine"].get("llm_id")
        
        phone_number = None
        if agent_info:
            phone_number = agent_info.get("phone_number")

        if phone_number:
            try:
                retell_request("PATCH", f"/update-phone-number/{phone_number}", {"inbound_agents": []})
            except Exception as e_phone:
                logger.warning(f"No se pudo desvincular el teléfono del agente {agent_id}: {e_phone}")

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


# ==================== ENDPOINTS GENERALES ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args", data)
        calendar_email = args.get("calendar_email")
        start_time_str = args.get("start_time")
        descripcion_final = args.get("datos_cliente_recolectados", "")
        if args.get("description"):
            descripcion_final += " | " + args.get("description", "")

        create_google_event(
            calendar_email, 
            args.get("summary", "Cita Agendada"), 
            start_time_str, 
            None,                    # Forzamos None para que siempre use duracion_cita
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
            "🧪 Prueba de Acceso", 
            (datetime.now(MADRID_TZ) + timedelta(days=30)).isoformat(), 
            (datetime.now(MADRID_TZ) + timedelta(days=30, minutes=15)).isoformat(), 
            "Prueba técnica", 
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

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Email requerido")
        token = create_magic_token(email)
        magic_link = f"https://retell-bot.onrender.com/login?token={token}"
        enviado = send_magic_link_email(email, magic_link)
        if enviado:
            return {"status": "success", "message": "Enlace mágico enviado correctamente."}
        else:
            raise HTTPException(status_code=500, detail="Error al enviar el correo.")
    except Exception as e:
        logger.error(f"Error en request-magic-link: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== ENDPOINT CHAT GROK ====================
@app.post("/chat-grok")
async def chat_with_grok(request: Request):
    try:
        if not grok_client:
            raise HTTPException(status_code=500, detail="GROK_API_KEY no configurada")

        data = await request.json()
        user_message = data.get("message", "").strip()
        conversation_history = data.get("history", [])

        if not user_message:
            raise HTTPException(status_code=400, detail="Mensaje requerido")

        system_prompt = """Eres el Asistente Técnico de Dansu AI, un experto amable, paciente, profesional y cercano especializado en ayudar a dueños de negocios a conectar su CRM con su asistente telefónico virtual.

REGLAS OBLIGATORIAS Y PRIORIDAD ABSOLUTA:

1. Primer Paso Obligatorio en TODA conversación nueva:
   - Siempre comienza guiando al usuario paso a paso para que comparta su calendario personal de Google con nuestra cuenta de servicio.
   - Explícale claramente que es obligatorio usar una cuenta personal de Google (no cuenta de empresa).

2. Instrucciones exactas:
   - Entra a tu Google Calendar personal.
   - Arriba a la izquierda pulsa '+' Crear → Nombre: 'Asistente Dansu' + zona horaria.
   - En 'Mis calendarios' → tres puntos → Configurar y compartir.
   - 'Añadir personas' → pega: asistente-virtual@asistente-virtual-500413.iam.gserviceaccount.com
   - Permisos: 'Hacer cambios y gestionar el uso compartido' → espera 5 minutos.

3. Después pregunta el CRM y guía paso a paso pidiendo confirmación."""

        messages = [
            {"role": "system", "content": system_prompt}
        ] + conversation_history + [{"role": "user", "content": user_message}]

        response = grok_client.chat.completions.create(
            model="grok-4.5",
            messages=messages,
            tools=[{"type": "function", "function": {"name": "web_search", "description": "Buscar información actualizada", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}],
            tool_choice="auto",
            temperature=0.7,
            max_tokens=2048
        )

        assistant_reply = response.choices[0].message.content

        return {
            "reply": assistant_reply,
            "history": conversation_history + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_reply}
            ]
        }

    except Exception as e:
        logger.error(f"❌ Error en /chat-grok: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
