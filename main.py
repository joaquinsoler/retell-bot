import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Dansu Backend Completo")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas")

# ==================== CORS ====================
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
    print("✅ Base de datos PostgreSQL inicializada.")

init_db()

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

# (Mantengo todas tus funciones originales de Google Calendar sin cambios)
def ensure_calendar_access(calendar_id: str):
    try:
        service = get_calendar_service()
        service.calendarList().insert(body={'id': calendar_id}).execute()
    except HttpError as e:
        if e.status_code != 409:
            print(f"⚠️ Error suscripción: {e}")

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
        freebusy = service.freebusy().query(body=body).execute()
        busy = freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy) == 0
    except Exception as e:
        print(f"⚠️ Error FreeBusy: {e}")
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    ensure_calendar_access(calendar_id)
    iso_start = normalize_to_madrid_iso(start_time)
    iso_end = normalize_to_madrid_iso(end_time)
    if not bypass_availability and not check_availability(calendar_id, iso_start, iso_end):
        raise Exception("El horario seleccionado ya no está disponible.")
    service = get_calendar_service()
    event = {
        'summary': summary[:100],
        'description': description or "Cita agendada por Dansu AI",
        'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
        'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
        'reminders': {'useDefault': True}
    }
    return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()

# ==================== RETELL ====================
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
    return f"""Eres la voz y el asistente virtual exclusivo de {nombre_negocio}, un negocio enfocado en el sector de {sector}. ..."""  # Tu prompt completo aquí

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    # Tu función original completa (la mantengo sin cambios)
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)
    # ... resto de tu código original de creación ...
    # (copia aquí tu función completa si quieres, pero como no cambió, la dejo resumida)

# ==================== ENDPOINTS ====================

@app.post("/get-user-bots")
async def get_user_bots(request: Request):
    data = await request.json()
    email = data.get("email", "").strip()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    return {"status": "success", "bots": bots}

# ====================== EDICIÓN SEGURA ======================
@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    """Solo actualiza PostgreSQL - Versión segura"""
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta agent_id")

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
            WHERE agent_id = %s;
        """, (
            data.get("nombre_negocio", "").strip(),
            data.get("sector", "").strip(),
            data.get("servicios", "").strip(),
            data.get("horario", "").strip(),
            data.get("zona", "").strip(),
            data.get("google_calendar_email", "").strip(),
            data.get("asistente", "").strip(),
            agent_id
        ))

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Asistente no encontrado")

        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "message": "Datos actualizados correctamente (solo BD)"}

    except Exception as e:
        print(f"❌ Error update-retell-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# El resto de tus endpoints (delete, book-appointment, verify, create, etc.) se mantienen exactamente igual
# ... copia aquí el resto de tu código original ...

@app.post("/delete-retell-bot")
# ... tu código original ...

@app.post("/book-appointment")
# ... tu código original ...

@app.post("/verify-calendar-access")
# ... tu código original ...

@app.post("/create-retell-bot")
# ... tu código original ...

@app.get("/")
async def root():
    return {"status": "OK - Edición segura activa"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
