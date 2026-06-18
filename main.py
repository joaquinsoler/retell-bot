from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI()

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
if not RETELL_API_KEY:
    raise Exception("RETELL_API_KEY no encontrada")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials)

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = ""):
    service = get_calendar_service()
    event = {
        'summary': summary,
        'description': description,
        'start': {'dateTime': start_time, 'timeZone': 'Europe/Madrid'},
        'end': {'dateTime': end_time, 'timeZone': 'Europe/Madrid'},
        'reminders': {'useDefault': True}
    }
    return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='all').execute()

# === VOICE MAPPING y funciones Retell (las mismas que tenías) ===
VOICE_MAPPING = { ... }  # Mantén tu diccionario completo

def retell_request(...):  # Mantén tu función

def create_bot_for_client(...):  # Mantén tu función (o actualízala si quieres)

# ==================== ENDPOINTS ====================

@app.post("/create-retell-bot")
async def wix_webhook(request: Request):
    # ... (mantén igual, solo asegúrate de recibir calendar_name)
    pass  # Usa la versión anterior que tenías y añade calendar_name al return

@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        calendar_name = data.get("calendar_name", "").strip() or "primary"

        service = get_calendar_service()

        # Buscar el calendario correcto
        calendars = service.calendarList().list().execute()
        calendar_id = "primary"

        for cal in calendars.get('items', []):
            if (calendar_name.lower() in cal['summary'].lower()) or cal.get('id') == calendar_email:
                calendar_id = cal['id']
                print(f"✅ Usando calendario: {cal['summary']} ({calendar_id})")
                break

        event = create_google_event(
            calendar_id=calendar_id,
            summary="🧪 Prueba de conexión - Dansu",
            start_time="2026-07-01T10:00:00+02:00",
            end_time="2026-07-01T10:30:00+02:00",
            description=f"Prueba Dansu - {calendar_name}"
        )

        return {"status": "success", "message": "Conexión verificada"}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise HTTPException(status_code=403, detail="No se pudo acceder al calendario. Revisa nombre y permisos.")

@app.get("/")
async def root():
    return {"status": "OK - Soporte calendar_name activo"}
