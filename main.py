import os
import json
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
from jose import JWTError, jwt

app = FastAPI(title="Dansu Backend Completo")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas (RETELL_API_KEY, GOOGLE_CREDENTIALS o DATABASE_URL)")

if not JWT_SECRET_KEY:
    raise Exception("Falta la variable de entorno JWT_SECRET_KEY")

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15

# Almacén temporal en memoria para verificar al usuario por su dirección IP
SESIONES_ACTIVAS = {}

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
    print("✅ Base de datos PostgreSQL inicializada y lista.")

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
Responde con un tono comercial impecable explicando tus límites. (Ej: *"Actualmente solo puedo facilitarte información y agendar nuevas citas en el sistema. Para cancelar o modificar una reserva que ya tienes, te sugiero ponerte en contacto directamente con nuestro equipo técnico o de atención humana a través de nuestros canales habituales, y ellos lo resolverán encantados."*).
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


def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)
    
    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda una nueva cita directamente en el Google Calendar asignado.",
            "url": "https://retell-bot.onrender.com/book-appointment",
            "parameters": {
                "type": "object",
                "properties": {
                    "calendar_email": {"type": "string", "description": "Email institucional exacto proporcionado en las directrices."},
                    "summary": {"type": "string", "description": "Formato estricto: 'Cita: [Nombre Cliente] - [Motivo Cita] [Teléfono]'"},
                    "start_time": {"type": "string", "description": "ISO String absoluto (ej: 2026-07-01T15:30:00)."},
                    "end_time": {"type": "string", "description": "ISO String calculando fin de cita exacto."}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time"]
            }
        }]
    })
    
    if not llm_res:
        return None
        
    llm_id = llm_res.get("llm_id")
    
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Dansu Agent - {nombre_negocio}",
        "llm_id": llm_id,
        "voice_id": voice_id,
        "language": "es"
    })
    
    if not agent_res:
        return None
        
    agent_id = agent_res.get("agent_id")
    
    phone_res = retell_request("POST", "/create-phone-number", {
        "phone_number_name": f"Phone - {nombre_negocio}"
    })
    
    phone_number = phone_res.get("phone_number") if phone_res else "No asignado (Límite de API)"
    
    if phone_res and agent_id:
        retell_request("PATCH", f"/update-phone-number/{phone_number}", {
            "agent_id": agent_id
        })
        
    return {
        "agent_id": agent_id,
        "phone_number": phone_number
    }


# ==================== NUEVA LOGICA DE AUTENTICACION ENLACE MAGICO ====================
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
    if not BREVO_API_KEY:
        print("❌ Faltan credenciales SMTP de Brevo")
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
                    </div>
                </body>
                </html>
            """
        }
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
            json=payload, timeout=15
        )
        return response.status_code in (200, 201)
    except Exception as e:
        print(f"❌ Error enviando mail por Brevo: {e}")
        return False


# ==================== ENDPOINTS DE CONTROL Y ASISTENTES ====================

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    print("\n--- 📥 SOLICITUD EN /request-magic-link ---")
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="Formato de email inválido")

        token = create_magic_token(email)
        magic_link = f"https://retell-bot.onrender.com/redirect-to-wix?token={token}"

        if send_magic_link_email(email, magic_link):
            return {"status": "success", "message": "Enlace enviado correctamente."}
        raise HTTPException(status_code=500, detail="Error al enviar el email.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/redirect-to-wix", response_class=HTMLResponse)
async def redirect_to_wix(token: str, request: Request):
    print(f"\n--- 🔀 PUENTE POR IP ACTIVADO ---")
    email = verify_magic_token(token)
    if not email:
        return "<html><body><h3>❌ El enlace no es válido o ha expirado. Por favor solicita uno nuevo.</h3></body></html>"
    
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    SESIONES_ACTIVAS[client_ip] = {
        "email": email,
        "expira": datetime.utcnow() + timedelta(minutes=5)
    }
    print(f"✅ IP {client_ip} vinculada temporalmente a {email}")
    
    wix_url = "https://www.dansu.info/blank-4"
    return f"<html><head><meta http-equiv='refresh' content='0;url={wix_url}'></head><body><h3>Autenticación exitosa. Cargando panel... 🚀</h3></body></html>"


@app.get("/check-session")
async def check_session(request: Request):
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    sesion = SESIONES_ACTIVAS.get(client_ip)
    
    if not sesion:
        return {"status": "no_session"}
        
    if datetime.utcnow() > sesion["expira"]:
        del SESIONES_ACTIVAS[client_ip]
        return {"status": "no_session"}
        
    email = sesion["email"]
    del SESIONES_ACTIVAS[client_ip]  # Consumimos un único uso por seguridad
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    
    return {"status": "success", "email": email, "bots": bots}


@app.post("/get-asistentes")
@app.post("/get-asistentes/")
async def get_asistentes(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email", "").strip().lower()
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (calendar_email,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/update-asistente")
@app.post("/update-asistente/")
async def update_asistente(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes
            SET nombre_negocio = %s,
                sector = %s,
                servicios = %s,
                horario = %s,
                zona = %s,
                google_calendar_email = %s,
                asistente = %s
            WHERE agent_id = %s
            RETURNING *;
        """, (
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), data.get("google_calendar_email"),
            data.get("asistente"), agent_id
        ))
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        if not updated:
            raise HTTPException(status_code=404, detail="Asistente no encontrado.")
            
        # Sincronizamos cambios reales de prompt dinámico en Retell AI
        custom_prompt = build_custom_prompt(
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), data.get("google_calendar_email")
        )
        
        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if agent_info and "llm_id" in agent_info:
            retell_request("PATCH", f"/update-retell-llm/{agent_info['llm_id']}", {
                "general_prompt": custom_prompt
            })
            
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        retell_request("PATCH", f"/update-agent/{agent_id}", {
            "voice_id": voice_id
        })
        
        return {"status": "success", "bot": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete-asistente")
@app.post("/delete-asistente/")
async def delete_asistente_endpoint(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s RETURNING *;", (agent_id,))
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        if not deleted:
            raise HTTPException(status_code=404, detail="El asistente no existe en base de datos.")
            
        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if agent_info and "llm_id" in agent_info:
            retell_request("DELETE", f"/delete-retell-llm/{agent_info['llm_id']}")
            
        retell_request("DELETE", f"/delete-agent/{agent_id}")
        
        return {"status": "success", "message": "Eliminado correctamente de la infraestructura."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment_endpoint(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        summary = data.get("summary")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        
        if not all([calendar_email, summary, start_time, end_time]):
            return {"code": "ERROR", "message": "Faltan parámetros requeridos"}
            
        create_google_event(calendar_email, summary, start_time, end_time)
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
