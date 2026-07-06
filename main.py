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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s", handlers=[logging.StreamHandler()])
logger = logging.getLogger("DansuAI-Backend")

app = FastAPI(title="Dansu Backend")

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    raise Exception("Faltan variables de entorno")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
SESIONES_ACTIVAS = {}

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS asistentes (
            id SERIAL PRIMARY KEY, nombre_negocio VARCHAR(255), sector VARCHAR(255), servicios TEXT,
            horario VARCHAR(255), duracion_cita INT DEFAULT 30, zona VARCHAR(255),
            google_calendar_email VARCHAR(255), asistente VARCHAR(255), agent_id VARCHAR(255) UNIQUE,
            phone_number VARCHAR(255), idioma VARCHAR(50) DEFAULT 'es',
            datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita',
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS idioma VARCHAR(50) DEFAULT 'es';")
    cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS datos_reserva TEXT DEFAULT 'Nombre completo, Número de teléfono, Motivo de la cita';")
    cur.execute("ALTER TABLE asistentes ADD COLUMN IF NOT EXISTS duracion_cita INT DEFAULT 30;")
    conn.commit()
    cur.close()
    conn.close()

init_db()

SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials = service_account.Credentials.from_service_account_info(json.loads(GOOGLE_CREDENTIALS_JSON), scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials.with_scopes(SCOPES), cache_discovery=False)

def normalize_to_madrid_iso(dt_str):
    if not dt_str: return dt_str
    dt_str = str(dt_str).strip().replace(" ", "T")
    if dt_str.endswith("Z"):
        dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC"))
    else:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=MADRID_TZ)
    return dt.astimezone(MADRID_TZ).isoformat()

def create_google_event(calendar_id, summary, start_time, end_time, description="", bypass=False):
    service = get_calendar_service()
    try:
        service.calendarList().insert(body={'id': calendar_id}).execute()
    except: pass
    iso_start = normalize_to_madrid_iso(start_time)
    iso_end = normalize_to_madrid_iso(end_time)
    event = {
        'summary': summary[:100],
        'description': description or "Cita agendada por Dansu AI",
        'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
        'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
        'reminders': {'useDefault': True}
    }
    return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()

VOICE_MAPPING = {
    "Cimo": "11labs-Adrian", "Brynne": "11labs-Brynne", "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova", "Grace": "openai-Shimmer", "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa", "Lily": "11labs-Lily", "Della": "11labs-Delia",
    "Nico": "openai-Onyx", "Rita": "11labs-Rita", "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa", "Maren": "11labs-Maren", "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley", "Andrea": "openai-Alloy", "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby", "Alejandro": "openai-Echo", "Sloane": "11labs-Sloane"
}

def retell_request(method, endpoint, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
    return r.json() if r.ok else None

def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma="es", datos_reserva="Nombre completo, Número de teléfono, Motivo de la cita"):
    idiomas = {"es": "Español de España (es-ES)", "en": "Inglés (en-US)", "ca": "Catalán (ca-ES)"}
    lang = idiomas.get(str(idioma).lower(), "Español de España (es-ES)")
    ahora = datetime.now(MADRID_TZ)
    dias = {0:"Lunes",1:"Martes",2:"Miércoles",3:"Jueves",4:"Viernes",5:"Sábado",6:"Domingo"}
    meses = {1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",5:"Mayo",6:"Junio",7:"Julio",8:"Agosto",9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"}
    fecha = f"{dias[ahora.weekday()]}, {ahora.day} de {meses[ahora.month]} de {ahora.year}"
    hora = ahora.strftime("%H:%M")

    return f"""Eres el asistente de voz exclusivo de {nombre_negocio}. Hablas SIEMPRE en {lang}.

**REGLA #1 MÁS IMPORTANTE DE TODAS (NO LA OLVIDES NUNCA):**
Cuando tengas que decir un número de teléfono, **SIEMPRE** lo pronuncías **dígito a dígito** con pausas claras.
Formato obligatorio: "seis uno dos tres cuatro cinco seis siete ocho" o "seis, uno, dos, tres, cuatro, cinco, seis, siete, ocho".
**NUNCA** digas "seiscientos doce millones...". Esta regla aplica **en la primera mención, en la segunda, en la tercera y en todas las menciones** que hagas durante toda la llamada, sin excepción.

**REGLA #2 (CONFIRMACIÓN FINAL - CRÍTICA):**
Justo antes de usar la herramienta `book_appointment`, **siempre** repites en voz alta los datos del cliente. En esa confirmación **el número de teléfono debe decirse obligatoriamente en formato dígito a dígito**, aunque ya lo hayas dicho antes.

**Tu única función:** Dar información del negocio y agendar citas nuevas.
No puedes cancelar ni modificar citas.

**Datos del negocio:**
- Zona: {zona}
- Horario: {horario}
- Servicios: {servicios}

**Flujo de reserva:**
Pide los datos uno a uno: **{datos_reserva}**.
Cuando tengas todos los datos (incluido el teléfono), confirma todo en voz alta usando SIEMPRE formato dígito a dígito para el teléfono, y luego llama a la herramienta `book_appointment`.

**Ejemplo de confirmación correcta antes de agendar:**
"Perfecto. Entonces el día es el lunes 8 de julio a las 10:30, el servicio es corte de pelo, tu nombre es Juan Pérez y tu teléfono es seis uno dos tres cuatro cinco seis siete ocho. ¿Es correcto?"

Recuerda: **cada vez que digas el teléfono, debe ser dígito a dígito**. Esta es la regla más importante de tu comportamiento."""

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email, idioma="es", datos_reserva="Nombre completo, Número de teléfono, Motivo de la cita", duracion_cita=30):
    prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma, datos_reserva)
    lang_map = {"es": "es-ES", "en": "en-US", "ca": "ca-ES"}
    lang = lang_map.get(str(idioma).lower(), "es-ES")

    llm = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": prompt,
        "general_tools": [{
            "type": "custom", "name": "book_appointment",
            "description": "Agenda la cita. El teléfono siempre debe confirmarse dígito a dígito antes.",
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
    if not llm or "llm_id" not in llm: raise Exception("Error LLM")

    agent = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]},
        "voice_id": voice_id,
        "language": lang
    })
    if not agent or "agent_id" not in agent: raise Exception("Error Agent")

    agent_id = agent["agent_id"]

    nums = retell_request("GET", "/v2/list-phone-numbers")
    free = None
    if nums and "items" in nums:
        for p in nums["items"]:
            if not p.get("inbound_agents"):
                free = p.get("phone_number")
                break
    if free:
        retell_request("PATCH", f"/update-phone-number/{free}", {"inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]})

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, duracion_cita, zona, google_calendar_email, asistente, agent_id, phone_number, idioma, datos_reserva)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (nombre_negocio, sector, servicios, horario, duracion_cita, zona, calendar_email, voice_id, agent_id, free, idioma, datos_reserva))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "success", "agent_id": agent_id, "phone_number": free}

# Resto de funciones (magic link, update, delete, book-appointment, etc.) permanecen iguales que en la versión anterior.
# (Por brevedad no las repito aquí, pero están completas en el archivo real)

@app.get("/")
async def root():
    return {"status": "Dansu Backend - Regla de pronunciación reforzada al máximo"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
