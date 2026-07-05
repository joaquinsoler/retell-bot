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

app = FastAPI(title="Dansu Backend Completo con Magic Link")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    logger.critical("Faltan variables de entorno críticas")
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

# ==================== DB ====================
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
        conn.commit()
        logger.info("✅ Base de datos inicializada")
    except Exception as e:
        logger.error(f"Error DB: {e}")
    finally:
        cur.close()
        conn.close()

init_db()

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=SCOPES
    )
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
    try:
        if dt_str.endswith("Z"):
            dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
        return dt.astimezone(MADRID_TZ).isoformat()
    except Exception:
        return dt_str

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, 
                       description: str = "", customer_data: dict = None, bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)

        # === MEJORA: Construimos una descripción rica con toda la info del cliente ===
        final_description = description or ""
        
        if customer_data:
            lines = ["**Datos del cliente:**"]
            for key, value in customer_data.items():
                if value and key not in ["calendar_email", "summary", "start_time", "end_time"]:
                    lines.append(f"• {key.replace('_', ' ').title()}: {value}")
            if lines:
                final_description = "\n\n".join([final_description, "\n".join(lines)]).strip()

        if not final_description:
            final_description = "Cita agendada por Dansu AI"

        event = {
            'summary': summary[:100],
            'description': final_description,
            'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }

        service = get_calendar_service()
        created = service.events().insert(
            calendarId=calendar_id, 
            body=event, 
            sendUpdates='none'
        ).execute()

        logger.info(f"✅ Evento creado en Google Calendar: {created.get('htmlLink')}")
        return created

    except Exception as e:
        logger.error(f"❌ Error Google Calendar: {e}", exc_info=True)
        raise

# ==================== (resto de funciones sin cambios: VOICE_MAPPING, retell_request, build_custom_prompt, etc.) ====================

# ... [Mantén exactamente igual todo el código desde VOICE_MAPPING hasta el final de update-retell-bot, delete, etc.]

# ==================== ENDPOINT BOOK-APPOINTMENT CORREGIDO ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        
        # Retell envía los parámetros dentro de "args" o directamente
        args = data.get("args", data)

        # Extraemos todo lo que el asistente haya enviado
        calendar_email = args.get("calendar_email")
        summary = args.get("summary")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        description = args.get("description", "")

        # Todo lo demás que envíe el LLM (nombre, teléfono, motivo, email, etc.) lo metemos como customer_data
        customer_data = {k: v for k, v in args.items() if k not in 
                        ["calendar_email", "summary", "start_time", "end_time", "description"]}

        if not all([calendar_email, summary, start_time, end_time]):
            raise HTTPException(400, "Faltan parámetros obligatorios para crear la cita")

        create_google_event(
            calendar_id=calendar_email,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            customer_data=customer_data  # ← Aquí va toda la info que pidió el asistente
        )

        return {"code": "SUCCESS", "message": "Cita agendada correctamente en Google Calendar"}

    except Exception as e:
        logger.error(f"❌ ERROR EN BOOK-APPOINTMENT: {e}", exc_info=True)
        return {"code": "ERROR", "message": str(e)}


# ==================== (Mantén el resto de endpoints exactamente igual) ====================

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
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error verify-calendar: {e}")
        raise HTTPException(400, str(e))


# ... (el resto del código: create-retell-bot, magic link, etc. se mantiene igual)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
