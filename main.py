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

# ==================== LOGGING ROBUSTO ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')
logger = logging.getLogger("DansuBackend")

app = FastAPI(title="Dansu Backend - Corregido 2026")

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    raise Exception("Faltan variables de entorno")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
SESIONES_ACTIVAS = {}

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
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
    logger.info("DB inicializada")

init_db()

# ==================== GOOGLE CALENDAR ====================
# (funciones get_calendar_service, ensure_calendar_access, normalize..., check_availability, create_google_event - iguales a tu versión original)

SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

# ... (pega aquí tus funciones de Google Calendar exactas)

# ==================== PROMPT REFUERZADO (AÑO 2026 + MEJOR FLUJO INICIAL) ====================
def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres el asistente virtual exclusivo y profesional de {nombre_negocio}, especializado en {sector}.

**CONTEXTO TEMPORAL IMPORTANTE:**
Estamos en el año 2026. La fecha actual es junio de 2026. Siempre usa el año 2026 para cualquier referencia de fecha o cálculo de días relativos (próximo martes, la semana que viene, etc.), salvo que el usuario indique explícitamente otro año.

**TU OBJETIVO:**
Atender con máxima amabilidad, empatía y profesionalidad. Ofrecer una experiencia humana excelente.

**REGLAS ESTRICTAS:**
- Solo puedes dar información del negocio y agendar **nuevas citas**.
- Si te piden cancelar, modificar o consultar una cita existente: explica educadamente que no tienes acceso y sugiere contactar al equipo humano.

**INFORMACIÓN DEL NEGOCIO:**
- Zona: {zona}
- Horario: {horario}
- Servicios: {servicios}
- Email Calendar: {calendar_email}

**COMPORTAMIENTO INICIAL (MUY IMPORTANTE):**
Al empezar la llamada, saluda cordialmente, preséntate brevemente y pregunta la intención del usuario:
"Hola, soy el asistente virtual de {nombre_negocio}. ¿En qué puedo ayudarte hoy? ¿Quieres información sobre nuestros servicios o prefieres reservar una cita?"

Luego sigue el flujo según la respuesta del usuario.

**FLUJO DE AGENDAMIENTO:**
Pregunta un dato a la vez. Confirma siempre lo que entendiste.
Solo cuando tengas nombre completo, teléfono, día/hora y servicio, usa la herramienta `book_appointment`.

**REGLAS DE COMUNICACIÓN:**
- Habla de forma natural, cálida y clara.
- Una pregunta a la vez.
- Nunca menciones código, errores técnicos ni sistemas internos.
- Si hay problema con disponibilidad: discúlpate, ofrece alternativas y mantén tono positivo."""

# ==================== RETELL ====================
VOICE_MAPPING = { ... }  # Tu diccionario original completo

def retell_request(method, endpoint, json_data=None):
    # Tu función original + logging mejorado
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        logger.info(f"Retell {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        logger.error(f"Retell error {endpoint}: {e}")
        return None

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    try:
        custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

        llm_res = retell_request("POST", "/create-retell-llm", {
            "model": "gpt-4.1-mini",
            "start_speaker": "agent",
            "general_prompt": custom_prompt,
            "general_tools": [ ... ]  # Tu tool book_appointment original
        })
        if not llm_res or "llm_id" not in llm_res:
            raise Exception("Error creando LLM")

        agent_res = retell_request("POST", "/create-agent", {
            "agent_name": f"Bot {nombre_negocio}",
            "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
            "voice_id": voice_id,
            "language": "es-ES"
        })
        if not agent_res or "agent_id" not in agent_res:
            raise Exception("Error creando Agent")

        agent_id = agent_res["agent_id"]

        # Número de teléfono (mejorado)
        phone_number = None
        numbers = retell_request("GET", "/v2/list-phone-numbers")
        if numbers and "items" in numbers:
            for p in numbers["items"]:
                if not p.get("inbound_agents"):
                    phone_number = p.get("phone_number")
                    retell_request("PATCH", f"/update-phone-number/{phone_number}", {
                        "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
                    })
                    break

        # INSERT CON LOGGING Y MANEJO DE ERRORES
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id, agent_id, phone_number))
            conn.commit()
            cur.close()
            conn.close()
            logger.info(f"✅ Asistente guardado en DB correctamente: {agent_id}")
        except Exception as db_err:
            logger.error(f"❌ Error INSERT en DB para {agent_id}: {db_err}")
            # Aún devolvemos éxito en Retell para que el usuario pueda usarlo

        return {"status": "success", "agent_id": agent_id, "phone_number": phone_number}

    except Exception as e:
        logger.error(f"❌ Error general create_bot_for_client: {e}")
        raise

# ==================== MAGIC LINK Y ENDPOINTS ====================
# (request-magic-link, redirect-to-wix, check-session, get-user-bots, update-retell-bot, delete-retell-bot, book-appointment, verify-calendar-access, create-retell-bot - todos con logging extra donde corresponde)

# ... (pega aquí el resto de tus endpoints originales, añadiendo logging en los críticos)

@app.get("/")
async def root():
    return {"status": "Dansu Backend Completo y Corregido - 2026"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
