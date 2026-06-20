import os
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import psycopg2  # Conector nativo de PostgreSQL
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Dansu Backend con PostgreSQL")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")

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

# Inicializamos la estructura de la base de datos
init_db()


# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']

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


def check_availability(calendar_id: str, start_time: str, end_time: str) -> bool:
    """
    Consulta la API FreeBusy de Google Calendar para verificar si el hueco está libre.
    Limpia y valida las fechas para evitar errores 400 Bad Request de Google.
    """
    try:
        service = get_calendar_service()
        
        # --- LIMPIEZA Y FORMATEO DE FECHAS ISO 8601 ---
        def format_iso_strict(dt_str: str) -> str:
            if not dt_str: return dt_str
            dt_str = str(dt_str).strip().replace(" ", "T")
            
            # Forzar desplazamiento si falta la zona horaria
            if "+" not in dt_str and not dt_str.endswith("Z") and dt_str.count("-") < 3:
                dt_str += "+02:00"
            return dt_str

        iso_start = format_iso_strict(start_time)
        iso_end = format_iso_strict(end_time)
        
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
        print("ℹ️ Permitiendo el agendamiento por seguridad (Fail-Safe) para no perder la cita.")
        return True


def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        
        # Validamos disponibilidad si no estamos forzando el bypass (ej: en pruebas estáticas)
        if not bypass_availability and not check_availability(calendar_id, start_time, end_time):
            raise Exception("El horario seleccionado ya no está disponible.")

        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': (description or "Cita agendada por Dansu AI"),
            'start': {'dateTime': start_time, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': end_time, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        created = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
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
    return f"""Eres el asistente virtual de {nombre_negocio} ({sector}).
**INFORMACIÓN CRÍTICA QUE NUNCA DEBES OLVIRAR NI INVENTAR:**
- El email del Google Calendar del negocio es exactamente: {calendar_email}
- Cuando uses la herramienta `book_appointment`, pon SIEMPRE este email en `calendar_email`: {calendar_email}
- Nunca inventes otro email.
- Ubicación / Zona: {zona}
- Horario de atención: {horario}
- Servicios que ofreces: {servicios}

**Flujo para agendar cita (pregunta uno por uno):**
1. Confirma día y hora con el usuario.
2. Pregunta: "¿Me puedes decir tu nombre completo?"
3. Pregunta: "¿Cuál es tu número de teléfono?"
4. Pregunta: "¿Cuál es el motivo de la cita?"
5. Solo después de tener los tres datos, llama a la herramienta `book_appointment`.

Si la herramienta `book_appointment` te devuelve un mensaje de error indicando que el horario no está disponible, infórmale amablemente al usuario y pídele que elija otro día o tramo horario."""


# ==================== LÓGICA DE CREACIÓN ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda la cita en el calendario del negocio. Si el hueco está ocupado, devolverá un error.",
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

    if not llm_res or "llm_id" not in llm_res: raise Exception("Error creando LLM")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES"
    })

    if not agent_res or "agent_id" not in agent_res: raise Exception("Error creando Agent")
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

    # Guardado permanente en nuestra tabla de PostgreSQL en Render
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
    """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id, agent_id, free_number))
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}


# ==================== ENDPOINTS ÁREA DE CLIENTE ====================

@app.post("/get-user-bots")
async def get_user_bots(request: Request):
    """Obtiene los asistentes vinculados a un correo"""
    try:
        data = await request.json()
        email = data.get("email", "").strip()
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        return {"status": "success", "bots": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    """Actualiza la base de datos y reconfigura las instrucciones en vivo en Retell AI"""
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        nombre = data.get("nombre_negocio")
        sector = data.get("sector")
        servicios = data.get("servicios")
        horario = data.get("horario")
        zona = data.get("zona")
        calendar_email = data.get("google_calendar_email")

        if not agent_id: raise HTTPException(status_code=400, detail="Falta el agent_id")

        # 1. Recuperamos el agente de Retell para localizar su llm_id asignado
        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if not agent_info or "response_engine" not in agent_info:
            raise HTTPException(status_code=404, detail="Agente no encontrado en Retell")
        
        llm_id = agent_info["response_engine"].get("llm_id")
        
        # 2. Generamos el prompt modificado
        nuevo_prompt = build_custom_prompt(nombre, sector, servicios, horario, zona, calendar_email)
        
        # 3. Empujamos el prompt a Retell mediante API
        retell_request("PATCH", f"/update-retell-llm/{llm_id}", {
            "general_prompt": nuevo_prompt
        })

        # 4. Impactamos la actualización en Postgres
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio = %s, sector = %s, servicios = %s, horario = %s, zona = %s, google_calendar_email = %s
            WHERE agent_id = %s;
        """, (nombre, sector, servicios, horario, zona, calendar_email, agent_id))
        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "message": "Asistente modificado correctamente en el sistema y en Retell AI"}
    except Exception as e:
        print(f"❌ Error actualizando bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== ENDPOINTS DE AGENDAMIENTO Y TEST ====================

@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    print("\n" + "="*40 + " [RETELL JSON COMPLETO] " + "="*40)
    try:
        raw_body = (await request.body()).decode("utf-8")
        print(raw_body)
        print("="*104 + "\n")

        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args", data)

        event = create_google_event(
            args.get("calendar_email"), args.get("summary"),
            args.get("start_time"), args.get("end_time"), args.get("description", "")
        )
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}
    except Exception as e:
        print(f"❌ ERROR EN BOOK-APPOINTMENT: {e}")
        return {"code": "ERROR", "message": str(e)}


@app.post("/verify-calendar-access")
@app.post("/verify-calendar-access/")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        create_google_event(calendar_email, "🧪 Prueba de conexión - Dansu", "2026-07-01T10:00:00+02:00", "2026-07-01T10:30:00+02:00", bypass_availability=True)
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


@app.get("/")
async def root():
    return {"status": "Dansu Backend OK"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
