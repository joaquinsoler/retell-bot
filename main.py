import os
import json
import smtplib
from datetime import datetime
from zoneinfo import ZoneInfo  # Gestión nativa y precisa de zonas horarias en Python 3.9+
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import psycopg2  # Conector nativo de PostgreSQL
from psycopg2.extras import RealDictCursor
import jwt  # Para los enlaces mágicos seguros

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Dansu Backend Completo")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")

# Variables de entorno para enlaces mágicos vía Brevo API
BREVO_SMTP_USER = os.getenv("BREVO_SMTP_USER")
BREVO_SMTP_PASSWORD = os.getenv("BREVO_SMTP_PASSWORD")  # Funciona como API Key de Brevo
JWT_SECRET = os.getenv("JWT_SECRET", "una-clave-secreta-por-defecto-cambiala-en-render")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://dansu.info")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas (RETELL_API_KEY, GOOGLE_CREDENTIALS o DATABASE_URL)")

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
        conn.close()
        print("✅ Base de datos PostgreSQL inicializada y lista.")
    except Exception as e:
        print(f"❌ Error crítico inicializando la Base de Datos: {e}")

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
        print(f"✅ Calendario suscrito: {calendar_id}")
    except HttpError as e:
        if e.status_code == 409:
            print(f"ℹ️ Ya suscrito: {calendar_id}")
        else:
            print(f"⚠️ Error suscripción {e.status_code}: {e}")


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
        
        print(f"🔍 Consultando FreeBusy para {calendar_id} entre {iso_start} y {iso_end}")
        freebusy_query = service.freebusy().query(body=body).execute()
        busy_periods = freebusy_query.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        
        if busy_periods:
            print(f"❌ Hueco ocupado. Conflictos detectados: {busy_periods}")
            return False
            
        print("✅ Hueco 100% disponible.")
        return True
    except Exception as e:
        print(f"⚠️ Error al comprobar disponibilidad con FreeBusy: {e}")
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

        created = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates='none'
        ).execute()

        print(f"✅ EVENTO CREADO: {created.get('htmlLink')}")
        return created
    except Exception as e:
        print(f"❌ Error Google Calendar: {e}")
        raise


# ==================== VOICE MAPPING & RETELL UTILS ====================
VOICE_MAPPING = {
    "Cimo": "11labs-Adrian", 
    "Brynne": "11labs-Brynne", 
    "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova", 
    "Grace": "openai-Shimmer", 
    "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa", 
    "Lily": "11labs-Lily", 
    "Della": "11labs-Delia",
    "Nico": "openai-Onyx", 
    "Rita": "11labs-Rita", 
    "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa", 
    "Maren": "11labs-Maren", 
    "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley", 
    "Andrea": "openai-Alloy", 
    "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby", 
    "Alejandro": "openai-Echo", 
    "Sloane": "11labs-Sloane"
}


def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        print(f"→ Retell {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Error Retell: {e}")
        return None

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres la voz y el asistente virtual exclusivo de {nombre_negocio}, un negocio enfocado en el sector de {sector}.
Tu objetivo principal es atender a los clientes con la máxima amabilidad, empatía y profesionalidad, ofreciendo una conversación fluida, natural y cercana.

**ALCANCE DE TUS FUNCIONES (Muy Importante):**
- Tus únicas capacidades y tareas autorizadas son: **dar información detallada sobre el negocio** y **agendar nuevas citas**.
- Si el usuario te solicita cancelar una cita, eliminar una reserva existente, modificar un horario ya agendado o realizar cualquier otra gestión administrativa, debes aclararle de forma muy educada que no tienes acceso para realizar esa acción.
Responde con un tono comercial impecable explicando tus límites. (Ej: *"Actualmente solo puedo facilitarte información y agendar nuevas citas en el sistema. Para cancelar o modificar una reserva que ya tienes, te sugeriero ponerte en contacto directamente con nuestro equipo técnico o de atención humana a través de nuestros canales habituales, y ellos lo resolverán encantados."*).

**TU PERSONALIDAD Y TONO REQUERIDO:**
- Habla con calidez, usando frases cortas y claras para que la llamada sea cómoda.
Escucha activamente.
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
- Si la herramienta `book_appointment` te devuelve un fallo, un error del sistema o indica que el hueco está ocupado, actúa como un comercial humano resolutivo y amable. Gestiona la situación diciendo algo como: *"Disculpa las molestias, parece que este horario concreto acaba de ocuparse o no está disponible en nuestra agenda en este instante. Déjame revisar... ¿Te vendría bien intentar en otro tramo horario o preferirías mirar otro día?"*
- Si experimentas algún problema técnico interno con las herramientas, mantén la calma, discúlpate amablemente por la pequeña pausa y reconduce la llamada ofreciéndote a tomar nota manualmente o pedirle que lo intente en unos instantes, garantizando siempre una experiencia de atención al cliente excelente."""


# ==================== LÓGICA DE CREACIÓN ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
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
                    "start_time": {"type": "string", "description": "Formato ISO de Madrid YYYY-MM-DDTHH:MM:SS"},
                    "end_time": {"type": "string", "description": "Formato ISO de Madrid YYYY-MM-DDTHH:MM:SS"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time"]
            }
        }]
    })
    
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Fallo al crear el modelo LLM en Retell AI")
        
    llm_id = llm_res["llm_id"]
    
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Dansu Agent - {nombre_negocio}",
        "llm_id": llm_id,
        "voice_id": voice_id,
        "language": "es-ES"
    })
    
    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Fallo al inicializar el Agente de voz en Retell AI")
        
    agent_id = agent_res["agent_id"]
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *;
    """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, [k for k, v in VOICE_MAPPING.items() if v == voice_id][0], agent_id, "+34 900 000 000"))
    
    nuevo_bot = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    
    return nuevo_bot


# ==================== ENLACES MÁGICOS (BREVO API HTTP) ====================
def send_magic_link_email(to_email: str, link: str):
    """Envía el enlace mágico mediante la API HTTP de Brevo por el puerto web 443 (Garantiza el bypass de bloqueos en Render)"""
    print(f"🚀 Iniciando proceso de envío por API HTTP de Brevo a {to_email}...")
    
    if not BREVO_SMTP_USER or not BREVO_SMTP_PASSWORD:
        print("❌ ERROR CONFIGURACIÓN: BREVO_SMTP_USER o BREVO_SMTP_PASSWORD no están configuradas.")
        return False

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_SMTP_PASSWORD,  # La contraseña SMTP y la API Key son idénticas en Brevo
        "content-type": "application/json"
    }
    
    payload = {
        "sender": {
            "name": "Dansu AI",
            "email": BREVO_SMTP_USER
        },
        "to": [
            {
                "email": to_email
            }
        ],
        "subject": "✨ Tu enlace mágico de acceso - Dansu AI",
        "htmlContent": f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 500px; margin: 0 auto; padding: 25px; border: 1px solid #e2e8f0; border-radius: 16px; box-shadow: 0 4px 10px rgba(0,0,0,0.03);">
                    <h2 style="color: #0078FF; text-align: center; margin-bottom: 20px;">Acceso al Panel de Control</h2>
                    <p>Hola,</p>
                    <p>Has solicitado entrar a tu Área de Gestión de Asistentes en Dansu AI. Haz clic en el botón inferior para iniciar sesión automáticamente de forma segura. Este enlace caducará en 15 minutos.</p>
                    <div style="text-align: center; margin: 35px 0;">
                        <a href="{link}" style="background-color: #0078FF; color: white; padding: 14px 28px; text-decoration: none; font-weight: bold; border-radius: 10px; display: inline-block;">Entrar al Panel Directamente</a>
                    </div>
                    <p style="font-size: 12px; color: #666; margin-top: 25px;">Si el botón no responde, puedes copiar y pegar el siguiente enlace en tu navegador habitual:</p>
                    <p style="font-size: 11px; color: #0078FF; word-break: break-all;"><a href="{link}">{link}</a></p>
                    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin-top: 30px;">
                    <p style="font-size: 11px; color: #999; text-align: center;">Si tú no has iniciado esta acción, puedes ignorar este correo tranquilamente.</p>
                </div>
            </body>
        </html>
        """
    }

    try:
        print("🌐 Realizando llamada POST HTTPS a api.brevo.com por puerto web seguro...")
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"📊 Respuesta de la API de Brevo: Código {response.status_code}")
        
        if response.status_code in [200, 201, 202]:
            print(f"📧 ¡ÉXITO TOTAL VÍA API! Correo enviado con éxito a {to_email}")
            return True
        else:
            print(f"❌ Brevo API rechazó la petición: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error excepcional llamando a la API de Brevo: {e}")
        return False


# ==================== ENDPOINTS DE LA API (FASTAPI) ====================

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    """Endpoint para solicitar el envío del Magic Link por email aplicando TRIM e ignorando espacios invisibles"""
    print("\n--- 📥 NUEVA SOLICITUD DE MAGIC LINK RECIBIDA ---")
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        print(f"📋 Email saneado recibido en el body: '{email}'")
        
        if not email:
            print("⚠️ Validación fallida: El campo email está vacío.")
            raise HTTPException(status_code=400, detail="El email es un campo obligatorio.")
            
        print("🗄️ Conectando a la Base de Datos PostgreSQL para comprobar existencia...")
        conn = get_db_connection()
        cur = conn.cursor()
        
        query = "SELECT COUNT(*) FROM asistentes WHERE TRIM(LOWER(google_calendar_email)) = TRIM(%s);"
        print(f"🔍 Ejecutando Query SQL: {query} con parámetro '{email}'")
        cur.execute(query, (email,))
        count = cur.fetchone()['count']
        cur.close()
        conn.close()
        print(f"📊 Coincidencias encontradas en la Base de Datos: {count}")
        
        if count == 0:
            print(f"❌ Acceso Denegado: El email '{email}' no existe en la base de datos de asistentes.")
            raise HTTPException(status_code=404, detail="Este correo no está vinculado a ningún asistente operativo.")
            
        print("🪙 Generando Token firmado JWT (Válido por 15 minutos)...")
        payload = {
            "email": email,
            "exp": datetime.now(ZoneInfo("UTC")).timestamp() + 900
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        
        magic_link = f"{FRONTEND_URL}?token={token}"
        print(f"🔗 URL Mágica Estructurada: {magic_link}")
        
        email_enviado = send_magic_link_email(email, magic_link)
        
        if email_enviado:
            print("✅ Respuesta HTTP 200 enviada con éxito al cliente.")
            return {"status": "success", "message": "Enlace mágico enviado. Revisa tu bandeja de entrada."}
        else:
            print("❌ El envío falló en el módulo de la API. Elevando error 500 al cliente.")
            raise HTTPException(status_code=500, detail="No se pudo procesar el envío de correo. Revisa las claves de tu proveedor Brevo.")
            
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"💥 CRISIS CRÍTICA EN ENDPOINT /request-magic-link: {e}")
        raise HTTPException(status_code=500, detail=f"Fallo interno en el servidor: {str(e)}")


@app.post("/verify-magic-token")
async def verify_magic_token(request: Request):
    """Verifica el token JWT devuelto por el cliente y le concede el retorno de sus bots limpiando espacios"""
    try:
        data = await request.json()
        token = data.get("token")
        
        if not token:
            raise HTTPException(status_code=400, detail="Falta el token de acceso.")
            
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        email = payload.get("email")
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE TRIM(LOWER(google_calendar_email)) = TRIM(%s) ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()
        
        return {"status": "success", "email": email, "bots": bots}
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="El enlace mágico ha caducado. Vuelve a solicitar uno nuevo.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="El token del enlace mágico es inválido o se encuentra corrupto.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/get-asistentes/{email}")
@app.get("/get-asistentes/{email}/")
async def get_asistentes(email: str):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE TRIM(LOWER(google_calendar_email)) = TRIM(%s) ORDER BY id DESC;", (email.strip().lower(),))
        bots = cur.fetchall()
        cur.close()
        conn.close()
        return bots
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/update-retell-bot")
@app.post("/update-retell-bot/")
async def update_retell_bot(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        nombre_negocio = data.get("nombre_negocio")
        sector = data.get("sector")
        servicios = data.get("servicios")
        horario = data.get("horario")
        zona = data.get("zona")
        asistente = data.get("asistente")
        calendar_email = data.get("google_calendar_email")

        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el agent_id")

        voice_id = VOICE_MAPPING.get(asistente)
        if not voice_id:
            raise HTTPException(status_code=400, detail=f"Voz '{asistente}' no soportada.")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE agent_id = %s;", (agent_id,))
        bot_existente = cur.fetchone()

        if not bot_existente:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Asistente no encontrado en base de datos")

        llm_id = None
        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if agent_info and "llm_id" in agent_info:
            llm_id = agent_info["llm_id"]

        nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

        if llm_id:
            retell_request("PATCH", f"/update-retell-llm/{llm_id}", {"general_prompt": nuevo_prompt})
        
        retell_request("PATCH", f"/update-agent/{agent_id}", {
            "agent_name": f"Dansu Agent - {nombre_negocio}",
            "voice_id": voice_id
        })

        cur.execute("""
            UPDATE asistentes
            SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, zona = %s, asistente = %s, google_calendar_email = %s
            WHERE agent_id = %s
            RETURNING *;
        """, (nombre_negocio, sector, servicios, horario, zona, asistente, calendar_email, agent_id))
        
        bot_actualizado = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "bot": bot_actualizado}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete-retell-bot")
@app.post("/delete-retell-bot/")
async def delete_retell_bot(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")

        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el agent_id")

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        llm_id = agent_info.get("llm_id") if agent_info else None

        retell_request("DELETE", f"/delete-agent/{agent_id}")
        if llm_id:
            retell_request("DELETE", f"/delete-retell-llm/{llm_id}")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "message": f"Agente {agent_id} y su LLM eliminados correctamente."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment_endpoint(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        summary = data.get("summary", "Cita agendada por IA")
        start_time = data.get("start_time")
        end_time = data.get("end_time")

        if not calendar_email or not start_time or not end_time:
            return {"code": "INVALID_PARAMS", "message": "Faltan parámetros críticos (calendar_email, start_time, end_time)"}

        created_event = create_google_event(
            calendar_id=calendar_email,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description="Reserva agendada de forma automática por el asistente de voz de Dansu AI."
        )
        return {"code": "SUCCESS", "event_link": created_event.get("htmlLink")}
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
        return {"status": "success", "message": "Acceso verificado correctamente"}
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
