import os
import json
import logging
import re
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

# ==================== CONFIGURACIÓN DE LOGS PARA RENDER ====================
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
    logger.critical("Faltan variables de entorno críticas en el despliegue.")
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
                duracion_cita VARCHAR(255) DEFAULT '30',
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
        conn.commit()
    finally:
        cur.close()
        conn.close()

init_db()

MADRID_TZ = ZoneInfo("Europe/Madrid")

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str: return dt_str
    dt_str = str(dt_str).strip().replace(" ", "T").replace("Z", "")
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None: dt = dt.replace(tzinfo=MADRID_TZ)
    return dt.astimezone(MADRID_TZ).isoformat()

# ... (Mantenemos tus funciones get_calendar_service, ensure_calendar_access, check_availability, create_google_event, retell_request, build_custom_prompt, create_bot_for_client, etc.) ...

# ==================== ENDPOINT CORREGIDO ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args", data)
        calendar_email = args.get("calendar_email")
        start_time_str = args.get("start_time")
        raw_datos = args.get("datos_cliente_recolectados", "")

        # 1. Normalizar inicio y validar pasado
        start_iso = normalize_to_madrid_iso(start_time_str)
        start_dt = datetime.fromisoformat(start_iso).astimezone(MADRID_TZ)
        if start_dt < (datetime.now(MADRID_TZ) - timedelta(minutes=2)):
            return {"code": "ERROR", "message": "La fecha seleccionada ya ha pasado."}

        # 2. Obtener duración real de DB
        duracion_minutos = 30
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT duracion_cita FROM asistentes WHERE google_calendar_email = %s LIMIT 1;", (calendar_email,))
                    res = cur.fetchone()
                    if res: duracion_minutos = int(str(res["duracion_cita"]).strip())
        except: pass

        # 3. CÁLCULO EXACTO SIN DESFASES
        end_dt = start_dt + timedelta(minutes=duracion_minutos)
        end_time_str = end_dt.astimezone(MADRID_TZ).isoformat()

        # 4. Limpieza y formato (igual al tuyo)
        datos_limpios = re.sub(r'(?:\d\s*,\s*){3,}\d', lambda m: m.group(0).replace(",", "").replace(" ", ""), raw_datos)
        
        # ... (Aquí iría tu lógica de construcción de 'descripcion_final') ...
        
        create_google_event(calendar_email, args.get("summary", "Cita"), start_iso, end_time_str, descripcion_final)
        return {"code": "SUCCESS", "message": "Cita agendada"}
    except Exception as e:
        return {"code": "ERROR", "message": str(e)}

# ... (Resto de tus endpoints que ya funcionaban)
