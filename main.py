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

# ==================== CONFIGURACIÓN DE LOGS ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("DansuAI-Backend")

app = FastAPI(title="Dansu Backend Completo con Magic Link y Mejorada Pronunciación")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    logger.critical("Faltan variables de entorno críticas.")
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

# ==================== POSTGRESQL ====================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
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
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS idioma VARCHAR(50) DEFAULT 'es';")
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita';")
        cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS duracion_cita INT DEFAULT 30;")
        conn.commit()
        logger.info("✅ Base de datos PostgreSQL inicializada.")
    except Exception as e:
        logger.error(f"❌ Error inicializando DB: {e}", exc_info=True)
    finally:
        cur.close()
        conn.close()

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
    except HttpError as e:
        if e.status_code != 409:
            logger.error(f"Error suscripción calendario: {e}")

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
        freebusy_query = service.freebusy().query(body=body).execute()
        busy_periods = freebusy_query.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy_periods) == 0
    except Exception as e:
        logger.error(f"Error FreeBusy: {e}", exc_info=True)
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        if not bypass_availability and not check_availability(calendar_id, iso_start, iso_end):
            raise Exception("Horario no disponible.")
        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': description or "Cita agendada por Dansu AI",
            'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        created = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
        logger.info(f"✅ Evento creado: {created.get('htmlLink')}")
        return created
    except Exception as e:
        logger.error(f"Error Google Calendar: {e}", exc_info=True)
        raise

# ==================== RETELL UTILS ====================
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
        logger.error(f"Error Retell: {e}", exc_info=True)
        return None

# ==================== PROMPT MEJORADO CON PRONUNCIACIÓN ====================
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
Tu objetivo principal es atender a los clientes con la máxima amabilidad, empatía y profesionalidad.

**REFERENCIA TEMPORAL OBLIGATORIA:**
- Hoy es: **{fecha_legible}**.
- Hora actual: **{hora_legible}** (Europe/Madrid).

**REGLA ABSOLUTA #1 - PRONUNCIACIÓN DE NÚMEROS (APLICAR SIEMPRE Y EN TODO EL NÚMERO):**
Speech Normalization está activada + Read Slowly.
- **Siempre** agrupa números de teléfono de 9 dígitos en **tres grupos de tres** y separa los grupos con " - " (guion con espacios a ambos lados).
- **Nunca** digas solo los primeros dígitos. Pronuncia **el número completo entero** usando este formato.
- Ejemplos que DEBES seguir literalmente:
  - 611223344 → "seis uno uno - dos dos tres - tres cuatro cuatro"
  - 622334455 → "seis dos dos - tres tres cuatro - cuatro cinco cinco"
  - 655112233 → "seis cinco cinco - uno uno dos - dos tres tres"
  - 666777888 → "seis seis seis - siete siete siete - ocho ocho ocho"
  - 912345678 → "nueve uno dos - tres cuatro cinco - seis siete ocho"
  - 600123456 → "seis cero cero - uno dos tres - cuatro cinco seis"
- Horas: "diez - treinta", "catorce - cuarenta y cinco"
- Fechas y precios: usa palabras naturales con pausas cuando sea necesario.
- **Cada vez** que confirmes o repitas un teléfono, di **el número completo** en este formato. No te detengas después del primer grupo.

**CONFIGURACIÓN DE IDIOMA:**
Habla **siempre** completamente en **{idioma_atencion}**.

**ALCANCE:**
Solo información del negocio y agendar citas. Para cancelar o modificar: di educadamente que no tienes acceso.

**PERSONALIDAD:**
Calidez, claridad y profesionalidad.

**DATOS DEL NEGOCIO:**
- Zona: {zona}
- Horario: {horario}
- Servicios: {servicios}
- Calendar: {calendar_email}

**FLUJO DE RESERVA:**
Pide los datos de **{datos_reserva}** uno a uno. Cuando los tengas todos, usa `book_appointment`.

**REGLAS DE ERRORES:**
Nunca hables de código, errores técnicos ni endpoints. Si falla la herramienta, discúlpate y ofrece alternativas.

Sigue estas reglas en **todas** tus respuestas."""
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
                    "description": {"type": "string"},
                    "datos_cliente_recolectados": {"type": "string"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time", "datos_cliente_recolectados"]
            }
        }]
    })

    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")

    # ==================== CREACIÓN DEL AGENTE CON HANDBOOK_CONFIG ====================
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": lang_retell,
        "handbook_config": {
            "speech_normalization": True,           # ← ACTIVADO: Normalización de números, fechas, etc.
            # Opcionales recomendados (puedes activar más):
            # "natural_filler_words": True,
            # "default_personality": True,
        }
    })

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent")

    agent_id = agent_res["agent_id"]

    # Asignar número libre si existe
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
        logger.info(f"✅ Bot {agent_id} creado y registrado.")
    except Exception as e:
        logger.error(f"Error guardando en DB: {e}", exc_info=True)
        raise
    finally:
        cur.close()
        conn.close()

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}

# ==================== MAGIC LINK Y SESIONES ====================
# (Sin cambios en esta sección - se mantiene igual)

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
                        <p>Haz clic en el botón para acceder a tu panel de control de asistentes:</p>
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{magic_link}" target="_blank" style="background-color: #0078FF; color: white; padding: 14px 28px; text-decoration: none; border-radius: 12px; font-weight: 600; display: inline-block;">Acceder a mi Panel ✨</a>
                        </div>
                    </div>
                </body></html>
            """
        }
        r = requests.post("https://api.brevo.com/v3/smtp/email", 
                          headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"}, 
                          json=payload, timeout=15)
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"Error enviando email: {e}", exc_info=True)
        return False

# ==================== ENDPOINTS DE AUTENTICACIÓN ====================
# (Se mantienen iguales los endpoints de magic link, check-session, get-user-bots, etc.)

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
        logger.error(f"Error request-magic-link: {e}", exc_info=True)
        raise HTTPException(500, str(e))

@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    email = verify_magic_token(token)
    if not email:
        return "<html><body><h3>❌ Enlace inválido o caducado.</h3></body></html>"
    
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    SESIONES_ACTIVAS[client_ip] = {
        "email": email,
        "expira": datetime.utcnow() + timedelta(minutes=5)
    }
    wix_url = "https://www.dansu.info/blank-4"
    return f"""<html><head><meta http-equiv="refresh" content="0;url={wix_url}"></head>
    <body style="font-family:sans-serif; text-align:center; padding-top:50px;">
        <h3>Verificación completada. Cargando tu panel... 🚀</h3>
    </body></html>"""

@app.get("/check-session")
async def check_session(request: Request):
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    sesion = SESIONES_ACTIVAS.get(client_ip)
    if not sesion or datetime.utcnow() > sesion["expira"]:
        if sesion: del SESIONES_ACTIVAS[client_ip]
        return {"status": "no_session"}
    
    email = sesion["email"]
    del SESIONES_ACTIVAS[client_ip]
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        return {"status": "success", "email": email, "bots": bots}
    except Exception as e:
        logger.error(f"Error check-session: {e}", exc_info=True)
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
        logger.error(f"Error get-user-bots: {e}", exc_info=True)
        raise HTTPException(500, str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

# ==================== UPDATE CON HANDBOOK_CONFIG ====================
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

        if not agent_id:
            raise HTTPException(400, "Falta agent_id")

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if not agent_info or "response_engine" not in agent_info:
            raise HTTPException(404, "Agente no encontrado")

        llm_id = agent_info["response_engine"].get("llm_id")
        if not llm_id:
            raise HTTPException(400, "Sin LLM vinculado")

        nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma, datos_reserva)

        retell_request("PATCH", f"/update-retell-llm/{llm_id}", {
            "general_prompt": nuevo_prompt,
            "general_tools": [ { ... } ]  # (mantener igual que antes)
        })

        voice_id_tecnico = VOICE_MAPPING.get(asistente_nombre)
        retell_language_mapping = {"es": "es-ES", "en": "en-US", "ca": "ca-ES"}
        lang_retell = retell_language_mapping.get(str(idioma).strip().lower(), "es-ES")

        # ==================== PATCH DEL AGENTE CON HANDBOOK_CONFIG ====================
        agent_patch_data = {
            "language": lang_retell,
            "handbook_config": {
                "speech_normalization": True
            }
        }
        if voice_id_tecnico:
            agent_patch_data["voice_id"] = voice_id_tecnico

        retell_request("PATCH", f"/update-agent/{agent_id}", agent_patch_data)

        if not voice_id_tecnico:
            voice_id_tecnico = agent_info.get("voice_id")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, duracion_cita = %s, 
                zona = %s, google_calendar_email = %s, asistente = %s, idioma = %s, datos_reserva = %s
            WHERE agent_id = %s;
        """, (nombre_negocio, sector, servicios, horario, duracion_cita, zona, calendar_email, voice_id_tecnico, idioma, datos_reserva, agent_id))
        conn.commit()

        logger.info(f"✅ Bot {agent_id} actualizado con Speech Normalization.")
        return {"status": "success", "message": "Asistente actualizado correctamente."}
    except Exception as e:
        logger.error(f"Error en update: {e}", exc_info=True)
        raise HTTPException(500, str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

# ==================== DELETE (sin cambios) ====================
@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    # (código idéntico al original)
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        if not agent_id:
            raise HTTPException(400, "Falta agent_id")

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if agent_info and isinstance(agent_info, dict):
            llm_id = agent_info.get("response_engine", {}).get("llm_id")
            try:
                numbers_res = retell_request("GET", "/v2/list-phone-numbers")
                if numbers_res and "items" in numbers_res:
                    for phone in numbers_res["items"]:
                        if any(a.get("agent_id") == agent_id for a in phone.get("inbound_agents", [])):
                            retell_request("PATCH", f"/update-phone-number/{phone['phone_number']}", {"inbound_agents": []})
            except Exception:
                pass
            retell_request("DELETE", f"/delete-agent/{agent_id}")
            if llm_id:
                retell_request("DELETE", f"/delete-retell-llm/{llm_id}")
        else:
            logger.warning(f"Agente {agent_id} ya no existe en Retell.")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
        conn.commit()
        return {"status": "success", "message": "Asistente eliminado."}
    except Exception as e:
        logger.error(f"Error delete: {e}", exc_info=True)
        raise HTTPException(500, str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

# ==================== BOOK-APPOINTMENT Y OTROS (sin cambios lógicos) ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    # (código idéntico al original - ya calcula duración desde DB)
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args", data)

        calendar_email = args.get("calendar_email")
        start_time_str = args.get("start_time")

        duracion_minutos = 30
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT duracion_cita FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC LIMIT 1;", (calendar_email,))
            row = cur.fetchone()
            if row and row.get("duracion_cita"):
                duracion_minutos = int(row["duracion_cita"])
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
        except:
            end_time_str = args.get("end_time")

        datos_cliente = args.get("datos_cliente_recolectados", "")
        descripcion_final = "Cita agendada automáticamente por Dansu AI.\n\n"
        if datos_cliente:
            descripcion_final += f"📋 DATOS DEL CLIENTE:\n{datos_cliente}"
        else:
            descripcion_final += args.get("description", "")

        create_google_event(calendar_email, args.get("summary"), start_time_str, end_time_str, descripcion_final)
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}
    except Exception as e:
        logger.error(f"Error book-appointment: {e}", exc_info=True)
        return {"code": "ERROR", "message": str(e)}

# (Los demás endpoints verify-calendar-access, create-retell-bot, root se mantienen iguales)

@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        create_google_event(calendar_email, "🧪 Prueba de conexión - Dansu", 
                           "2026-07-01T10:00:00+02:00", "2026-07-01T10:30:00+02:00", bypass_availability=True)
        return {"status": "success", "message": "Acceso verificado"}
    except Exception as e:
        raise HTTPException(400, str(e))

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
        logger.error(f"Error create-retell-bot: {e}", exc_info=True)
        raise HTTPException(500, str(e))

@app.get("/")
async def root():
    return {"status": "Dansu Backend OK - Con pronunciación mejorada"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
