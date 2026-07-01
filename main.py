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

from jose import JWTError, jwt  # Añadido para el manejo seguro de tokens del Magic Link

# ==================== LOGGING SETUP (ROBUSTO Y PRECISO PARA RENDER) ====================
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S.%f"
)
logger = logging.getLogger("dansu_backend")
logger.info("🚀 Dansu Backend iniciando con LOGGING DEBUG COMPLETO activado. Todos los eventos críticos de disponibilidad, FreeBusy, normalización de fechas y booking quedarán registrados con timestamps precisos para diagnóstico en Render.")

app = FastAPI(title="Dansu Backend Completo con Magic Link + Logging Robusto")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    logger.critical("❌ Faltan variables de entorno críticas (RETELL_API_KEY, GOOGLE_CREDENTIALS, DATABASE_URL, JWT_SECRET_KEY o BREVO_API_KEY)")
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
    """Crea la tabla de asistentes si no existe en PostgreSQL al arrancar"""
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
    logger.info("✅ Base de datos PostgreSQL inicializada y lista.")

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
        logger.info(f"✅ Calendario suscrito correctamente: {calendar_id}")
    except HttpError as e:
        if e.status_code == 409:
            logger.debug(f"ℹ️ Calendario ya suscrito previamente (409): {calendar_id}")
        else:
            logger.warning(f"⚠️ Error al suscribir calendario {calendar_id} → status={e.status_code}: {e}")


def normalize_to_madrid_iso(dt_str: str) -> str:
    """Normaliza cualquier string de fecha/hora al formato ISO con zona Europe/Madrid.
    Registra TODOS los pasos para diagnosticar problemas de formato que llegan desde el LLM de Retell."""
    original_input = dt_str
    if not dt_str:
        logger.debug("normalize_to_madrid_iso: input vacío → devolviendo vacío")
        return dt_str

    dt_str = str(dt_str).strip().replace(" ", "T")
    logger.debug(f"normalize_to_madrid_iso | INPUT original: '{original_input}' | CLEANED: '{dt_str}'")

    if dt_str.endswith("Z"):
        try:
            dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC"))
            logger.debug(f"normalize_to_madrid_iso | Detectado sufijo Z → interpretado como UTC: {dt}")
        except Exception as parse_err:
            logger.warning(f"normalize_to_madrid_iso | Error parseando con Z: {parse_err} → devolviendo original")
            return dt_str
    else:
        try:
            dt = datetime.fromisoformat(dt_str)
            had_tz = dt.tzinfo is not None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
                logger.debug(f"normalize_to_madrid_iso | Sin tzinfo → asumido Europe/Madrid: {dt}")
            else:
                logger.debug(f"normalize_to_madrid_iso | Ya tenía tzinfo={dt.tzinfo} → se mantendrá y convertirá")
        except ValueError as ve:
            logger.warning(f"normalize_to_madrid_iso | ❌ ValueError al parsear '{dt_str}': {ve} → devolviendo string ORIGINAL sin normalizar (posible causa de errores en FreeBusy)")
            return dt_str
        except Exception as e:
            logger.error(f"normalize_to_madrid_iso | Error inesperado parseando '{dt_str}': {e}", exc_info=True)
            return dt_str

    try:
        dt_madrid = dt.astimezone(MADRID_TZ)
        result = dt_madrid.isoformat()
        logger.debug(f"normalize_to_madrid_iso | ✅ OUTPUT final: '{result}' (original_tz={dt.tzinfo}, madrid={dt_madrid.tzinfo})")
        return result
    except Exception as conv_err:
        logger.error(f"normalize_to_madrid_iso | Error convirtiendo a Madrid tz: {conv_err}", exc_info=True)
        return dt_str


def check_availability(calendar_id: str, start_time: str, end_time: str) -> bool:
    """Consulta FreeBusy de Google Calendar.
    LOGGING EXTREMADAMENTE DETALLADO para diagnosticar por qué aparecen slots como ocupados cuando no deberían."""
    logger.info(f"🔍 CHECK_AVAILABILITY | calendar_id={calendar_id} | start_time_raw='{start_time}' | end_time_raw='{end_time}'")
    
    try:
        service = get_calendar_service()
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        
        logger.debug(f"check_availability | NORMALIZED → iso_start='{iso_start}' | iso_end='{iso_end}'")
        
        body = {
            "timeMin": iso_start,
            "timeMax": iso_end,
            "timeZone": "Europe/Madrid",
            "items": [{"id": calendar_id}]
        }
        
        logger.debug(f"check_availability | FreeBusy REQUEST BODY:\n{json.dumps(body, indent=2, ensure_ascii=False)}")
        
        freebusy_query = service.freebusy().query(body=body).execute()
        
        # Log completo de la respuesta para diagnóstico preciso
        logger.debug(f"check_availability | FreeBusy RAW RESPONSE:\n{json.dumps(freebusy_query, indent=2, default=str, ensure_ascii=False)}")
        
        busy_periods = freebusy_query.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        
        logger.info(f"check_availability | busy_periods DETECTED: count={len(busy_periods)} | periods={busy_periods}")
        
        if busy_periods:
            logger.warning(f"❌ check_availability → HUECO OCUPADO (busy_periods no vacío) para {calendar_id} entre {iso_start} y {iso_end}")
            return False
            
        logger.info(f"✅ check_availability → HUECO 100% LIBRE para {calendar_id} entre {iso_start} y {iso_end}")
        return True
        
    except Exception as e:
        logger.error(f"❌ check_availability | EXCEPCIÓN GRAVE durante FreeBusy query para {calendar_id}: {e}", exc_info=True)
        logger.warning("⚠️ check_availability | Por comportamiento legacy se asume DISPONIBLE (return True) tras error. Esto puede enmascarar problemas de formato/tz.")
        return True


def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    logger.info(f"📅 CREATE_GOOGLE_EVENT | calendar={calendar_id} | summary='{summary[:80]}...' | start='{start_time}' | end='{end_time}' | bypass_availability={bypass_availability}")
    
    try:
        ensure_calendar_access(calendar_id)
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        
        logger.debug(f"create_google_event | normalized → iso_start='{iso_start}' | iso_end='{iso_end}'")
        
        if not bypass_availability:
            is_available = check_availability(calendar_id, iso_start, iso_end)
            logger.info(f"create_google_event | Resultado check_availability = {is_available}")
            if not is_available:
                logger.warning("create_google_event | ❌ Slot marcado como ocupado → se lanza excepción controlada")
                raise Exception("El horario seleccionado ya no está disponible.")

        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': (description or "Cita agendada por Dansu AI"),
            'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }

        logger.debug(f"create_google_event | EVENT BODY que se enviará a Google:\n{json.dumps(event, indent=2, ensure_ascii=False)}")

        created = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates='none'
        ).execute()

        logger.info(f"✅ EVENTO CREADO EXITOSAMENTE en Google Calendar | htmlLink={created.get('htmlLink')} | id={created.get('id')} | calendar={calendar_id}")
        return created
        
    except Exception as e:
        logger.error(f"❌ create_google_event | FALLO al crear evento en {calendar_id}: {e}", exc_info=True)
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
        response_text = r.text[:400] if r.text else ""
        logger.debug(f"→ Retell {method} {endpoint} → status={r.status_code} | response_preview={response_text}")
        if not r.ok:
            logger.warning(f"⚠️ Retell {method} {endpoint} devolvió status no OK: {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        logger.error(f"❌ Error en retell_request {method} {endpoint}: {e}", exc_info=True)
        return None

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres la voz y el asistente virtual exclusivo de {nombre_negocio}, un negocio enfocado en el sector de {sector}. Tu objetivo principal es atender a los clientes con la máxima amabilidad, empatía y profesionalidad, offering una conversación fluida, natural y cercana.

**ALCANCE DE TUS FUNCIONES (Muy Importante):**
- Tus únicas capacidades y tareas autorizadas son: **dar información detallada sobre el negocio** y **agendar nuevas citas**.
- Si el usuario te solicita cancelar una cita, eliminar una reserva existente, modificar un horario ya agendado o realizar cualquier otra gestión administrativa, debes aclararle de forma muy educada que no tienes acceso para realizar esa acción. Responde con un tono comercial impecable explicando tus límites. (Ej: *"Actualmente solo puedo facilitarte información y agendar nuevas citas en el sistema. Para cancelar o modificar una reserva que ya tienes, te sugiero ponerte en contacto directamente con nuestro equipo técnico o de atención humana a través de nuestros canales habituales, y ellos lo resolverán encantados."*).

**TU PERSONALIDAD Y TONO REQUERIDO:** 
- Habla con calidez, usando frases cortas y claras para que la llamada sea cómoda. Escucha activamente.
- Muéstrate siempre servicial, educado y con un trato comercial impecable.

**INFORMACIÓN OPERATIVA DEL NEGOCIO (Estrictamente real, nunca inventes datos):**
- Ubicación / Zona de servicio: {zona}
- Horario comercial: {horario}
- Servicios ofrecidos: {servicios}
- Email del Google Calendar institucional: {calendar_email}

**FLUJO NATURAL PARA RECOGER DATOS Y AGENDAR CITA:**
Cuando un usuario esté interesado en reservar, avanza de manera conversacional, preguntando los datos uno a uno (nunca todos de golpe en una sola frase):
1. **Día y Hora:** Propón o confirma el momento de la cita según las preferencias del cliente.
2. **Nombre Completo:** Solicitado con educación (Ej: "¿Me indicas tu nombre completo, por favor?").
3. **Número de Teléfono:** Para asegurar el contacto con el negocio.
4. **Motivo de la Cita:** Consulta de manera cordial qué servicio de los que ofreces necesita.

Solo cuando tengas recopilados estos 4 datos de forma exitosa, utiliza la herramienta `book_appointment` pasando obligatoriamente el email `{calendar_email}` en el campo `calendar_email`.

**REGLAS CRÍTICAS DE CONTROL DE ERRORES (Capa de Privacidad de Desarrollo):**
- NUNCA menciones nombres de variables, formatos de código, mensajes de servidores, ni términos técnicos de software en la llamada (como "error de JSON", "función", "endpoint", "404", "500", "backend", o "respuesta incorrecta"). Está estrictamente prohibido.
- Si la herramienta `book_appointment` te devuelve un fallo, un error del sistema o indica que el hueco está ocupado, actúa como un comercial humano resolutivo y amable. Gestiona la situación diciendo algo como: 
  *"Disculpa las molestias, parece que este horario concreto acaba de ocuparse o no está disponible en nuestra agenda en este instante. Déjame revisar... ¿Te vendría bien intentar en otro tramo horario o preferirías mirar otro día?"*
- Si experimentas algún problema técnico interno con las herramientas, mantén la calma, discúlpate amablemente por la pequeña pausa y reconduce la llamada ofreciéndote a tomar nota manualmente o pedirle que lo intente en unos instantes, garantizando siempre una experiencia de atención al cliente excelente."""


# ==================== LÓGICA DE CREACIÓN ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    logger.info(f"🤖 CREANDO NUEVO BOT | negocio={nombre_negocio} | sector={sector} | calendar={calendar_email} | voice={voice_id}")
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

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
                    "description": {"type": "string"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time"]
            }
        }]
    })

    if not llm_res or "llm_id" not in llm_res:
        logger.error("❌ Error creando LLM en Retell AI")
        raise Exception("Error creando LLM")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES"
    })

    if not agent_res or "agent_id" not in agent_res:
        logger.error("❌ Error creando Agent en Retell AI")
        raise Exception("Error creando Agent")

    agent_id = agent_res["agent_id"]
    logger.info(f"✅ Agent creado en Retell: agent_id={agent_id}")

    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents"):
                free_number = p.get("phone_number")
                break

    if free_number:
        logger.info(f"📞 Asignando número telefónico libre {free_number} al agent {agent_id}")
        retell_request("PATCH", f"/update-phone-number/{free_number}", {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
        })
    else:
        logger.warning("⚠️ No se encontró número telefónico libre en la cuenta de Retell AI")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
    """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id, agent_id, free_number))
    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"✅ Bot registrado en PostgreSQL correctamente | agent_id={agent_id} | phone={free_number}")

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}


# ==================== UTILS TOKENS & EMAIL (MAGIC LINK) ====================
def create_magic_token(email: str):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    token = jwt.encode({"sub": email.lower(), "exp": expire}, JWT_SECRET_KEY, algorithm=ALGORITHM)
    logger.debug(f"Magic token creado para email={email} (expira en {ACCESS_TOKEN_EXPIRE_MINUTES} min)")
    return token

def verify_magic_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        logger.info(f"✅ Magic token verificado correctamente para email={email}")
        return email
    except JWTError as e:
        logger.warning(f"❌ Magic token inválido o expirado: {e}")
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
        success = r.status_code in (200, 201)
        if success:
            logger.info(f"✅ Magic link enviado por email a {email} vía Brevo")
        else:
            logger.error(f"❌ Error enviando magic link a {email} → Brevo status={r.status_code}")
        return success
    except Exception as e:
        logger.error(f"❌ Excepción enviando magic link a {email}: {e}", exc_info=True)
        return False


# ==================== ENDPOINTS DE AUTENTICACIÓN (MAGIC LINK POR IP) ====================

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email or "@" not in email:
            logger.warning(f"request-magic-link | Email inválido recibido: {email}")
            raise HTTPException(400, "Email inválido")

        token = create_magic_token(email)
        magic_link = f"https://retell-bot.onrender.com/redirect-to-wix?token={token}"

        logger.info(f"request-magic-link | Solicitud recibida para email={email}")
        if send_magic_link_email(email, magic_link):
            return {"status": "success", "message": "Enlace enviado de forma transaccional."}
        raise HTTPException(500, "Error enviando email.")
    except Exception as e:
        logger.error(f"request-magic-link | Error: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    email = verify_magic_token(token)
    if not email:
        logger.warning("redirect-to-wix | Token inválido o expirado")
        return "<html><body><h3>❌ El enlace es inválido o ha caducado. Por favor, solicita uno nuevo.</h3></body></html>"
    
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    
    SESIONES_ACTIVAS[client_ip] = {
        "email": email,
        "expira": datetime.utcnow() + timedelta(minutes=5)
    }
    
    logger.info(f"redirect-to-wix | Sesión creada para email={email} | IP={client_ip} (válida 5 min)")
    
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
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    sesion = SESIONES_ACTIVAS.get(client_ip)
    
    if not sesion:
        logger.debug(f"check-session | No hay sesión activa para IP={client_ip}")
        return {"status": "no_session"}
    
    if datetime.utcnow() > sesion["expira"]:
        del SESIONES_ACTIVAS[client_ip]
        logger.info(f"check-session | Sesión expirada para IP={client_ip}")
        return {"status": "no_session"}
        
    email = sesion["email"]
    del SESIONES_ACTIVAS[client_ip]  # Consumo de un solo uso por seguridad
    logger.info(f"check-session | Sesión válida consumida para email={email} | IP={client_ip}")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    
    logger.debug(f"check-session | Bots recuperados para {email}: {len(bots)} registros")
    return {"status": "success", "email": email, "bots": bots}


# ==================== ENDPOINTS ÁREA DE CLIENTE (ORIGINALES MANTENIDOS) ====================
@app.post("/get-user-bots")
async def get_user_bots(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip()
        logger.info(f"get-user-bots | Consulta de bots para email={email}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()
        return {"status": "success", "bots": bots}
    except Exception as e:
        logger.error(f"get-user-bots | Error: {e}", exc_info=True)
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

        logger.info(f"update-retell-bot | Actualizando agent_id={agent_id} | nuevo calendar={calendar_email}")

        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el agent_id")

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if not agent_info or "response_engine" not in agent_info:
            raise HTTPException(status_code=404, detail="No se encontró el agente en Retell AI")

        llm_id = agent_info["response_engine"].get("llm_id")
        if not llm_id:
            raise HTTPException(status_code=400, detail="El agente no dispone de un motor LLM vinculado")

        nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

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
                        "description": {"type": "string"}
                    },
                    "required": ["calendar_email", "summary", "start_time", "end_time"]
                }
            }]
        })
        
        if not llm_update:
            raise HTTPException(status_code=500, detail="Error al sincronizar cambios y herramientas funcionales con el motor de Retell AI")

        voice_id_tecnico = VOICE_MAPPING.get(asistente_nombre)
        if voice_id_tecnico:
            retell_request("PATCH", f"/update-agent/{agent_id}", {
                "voice_id": voice_id_tecnico
            })
            logger.info(f"ℹ️ Voz de Retell AI actualizada a: {voice_id_tecnico} para agent={agent_id}")
        else:
            voice_id_tecnico = agent_info.get("voice_id")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, zona = %s, google_calendar_email = %s, asistente = %s
            WHERE agent_id = %s;
        """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id_tecnico, agent_id))
        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"✅ update-retell-bot completado con éxito para agent_id={agent_id}")
        return {"status": "success", "message": "Asistente modificado con control de disponibilidad de agenda re-activado con éxito."}
    except Exception as e:
        logger.error(f"❌ Error en update-retell-bot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
                            logger.info(f"ℹ️ Número de teléfono {phone['phone_number']} liberado exitosamente (agent {agent_id})")
            except Exception as e_phone:
                logger.warning(f"⚠️ No se pudo liberar el teléfono para agent {agent_id}: {e_phone}")

            retell_request("DELETE", f"/delete-agent/{agent_id}")
            if llm_id:
                retell_request("DELETE", f"/delete-retell-llm/{llm_id}")
            logger.info(f"✅ Agent y LLM eliminados de Retell AI: {agent_id}")
        else:
            logger.info(f"ℹ️ El agente {agent_id} ya no existe en Retell AI. Procediendo a purgar Base de Datos directamente.")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"✅ Registro limpiado con éxito en PostgreSQL para: {agent_id}")
        return {"status": "success", "message": "Asistente eliminado de forma permanente de todos los sistemas."}

    except Exception as e:
        logger.error(f"❌ Error crítico en delete-retell-bot: {e}", exc_info=True)
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
            conn.commit()
            cur.close()
            conn.close()
            logger.warning(f"⚠️ Limpieza forzada en base de datos completada para {agent_id} tras error previo")
            return {"status": "success", "message": "Limpieza forzada en base de datos completada."}
        except Exception as db_err:
            logger.critical(f"💥 Fallo total e irrecuperable en DB al borrar {agent_id}: {db_err}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Fallo total e irrecuperable en DB: {str(db_err)}")


# ==================== ENDPOINTS GENERALES ORIGINALES ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    """Endpoint llamado por la herramienta custom de Retell LLM durante las llamadas.
    LOGGING COMPLETO para diagnosticar problemas de concurrencia y formato de fechas."""
    logger.info("📞 === BOOK_APPOINTMENT INVOCADO DESDE RETELL LLM ===")
    try:
        raw_body = (await request.body()).decode("utf-8")
        logger.debug(f"book-appointment | RAW BODY recibido (primeros 800 chars): {raw_body[:800]}")
        
        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args", data)

        calendar_email = args.get("calendar_email")
        summary = args.get("summary")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        description = args.get("description", "")

        logger.info(f"book-appointment | PARSED → calendar_email={calendar_email} | summary='{summary}' | start_time='{start_time}' | end_time='{end_time}'")

        event = create_google_event(
            calendar_email,
            summary,
            start_time,
            end_time,
            description
        )

        logger.info("✅ book-appointment → DEVOLVIENDO SUCCESS al LLM de Retell")
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}
    except Exception as e:
        logger.error(f"❌ ERROR EN BOOK-APPOINTMENT (devuelto al LLM): {e}", exc_info=True)
        return {"code": "ERROR", "message": str(e)}


@app.post("/verify-calendar-access")
@app.post("/verify-calendar-access/")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        logger.info(f"verify-calendar-access | Verificando acceso para calendar_email={calendar_email}")
        create_google_event(
            calendar_email,
            "🧪 Prueba de conexión - Dansu",
            "2026-07-01T10:00:00+02:00",
            "2026-07-01T10:30:00+02:00",
            bypass_availability=True
        )
        logger.info(f"✅ verify-calendar-access OK para {calendar_email}")
        return {"status": "success", "message": "Acceso verificado correctamente"}
    except Exception as e:
        logger.error(f"verify-calendar-access | Error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        logger.info(f"create-retell-bot | Nueva solicitud de creación | negocio={data.get('nombre_negocio')} | email={data.get('google_calendar_email')}")
        result = create_bot_for_client(
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email")
        )
        return result
    except Exception as e:
        logger.error(f"create-retell-bot | Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    logger.debug("GET / → healthcheck OK")
    return {"status": "Dansu Backend Completo OK con logging robusto"}


if __name__ == "__main__":
    import uvicorn
    logger.info("Iniciando servidor uvicorn en 0.0.0.0:8000 (modo desarrollo local)")
    uvicorn.run(app, host="0.0.0.0", port=8000)
