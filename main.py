from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import json
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI()   # ← Este nombre "app" es obligatorio para uvicorn main:app

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
if not RETELL_API_KEY:
    raise Exception("RETELL_API_KEY no encontrada")

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== GOOGLE CALENDAR ====================
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

# ==================== VOICE MAPPING ====================
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
    try:
        r = requests.request(method, url, headers=headers, json=json_data)
        print(f"→ {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Retell error: {e}")
        return None

# ==================== CREAR BOT (con fecha dinámica + personalidad) ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    ahora = datetime.now()
    fecha_actual = ahora.strftime("%A, %d de %B de %Y")
    hora_actual = ahora.strftime("%H:%M")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.

**Fecha y hora actual:** Hoy es {fecha_actual} y son las {hora_actual}.

Información de la empresa:
- Servicios: {servicios}
- Horario: {horario}
- Zona: {zona}

**Tu forma de ser:**
- Eres amable, cercano, agradable y profesional.
- Hablas con calidez y cercanía, como un gran recepcionista que realmente quiere ayudar.
- Usas un tono positivo, servicial y natural.

**Cómo agendar citas:**
- Muéstrate proactivo y servicial cuando el cliente quiera reservar.
- Pregunta día, hora aproximada, motivo y número de teléfono.
- Confirma los datos antes de agendar.
- Usa la herramienta `book_appointment` cuando todo esté confirmado."""

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4.1-mini",
        "general_prompt": custom_prompt
    })
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES",
        "tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda una cita en el calendario de Google",
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

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent")

    agent_id = agent_res["agent_id"]

    # Asignar número
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

    return {
        "status": "success",
        "agent_id": agent_id,
        "phone_number": free_number
    }

# ==================== ENDPOINTS ====================

@app.post("/create-retell-bot")
async def wix_webhook(request: Request):
    try:
        payload = await request.json()
        data = payload.get("data", payload)

        asistente = data.get("asistente")
        nombre_negocio = data.get("nombre_negocio")
        sector = data.get("sector")
        servicios = data.get("servicios")
        horario = data.get("horario")
        zona = data.get("zona")
        calendar_email = data.get("google_calendar_email")

        if not all([asistente, nombre_negocio, sector, servicios, horario, zona, calendar_email]):
            raise HTTPException(status_code=422, detail="Faltan campos")

        voice_id = VOICE_MAPPING.get(asistente, "openai-Alloy")

        resultado = create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email)
        resultado["calendar_email"] = calendar_email
        return resultado

    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    # (Mantengo la versión anterior que funcionaba)
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        calendar_name = data.get("calendar_name", "").strip() or "primary"

        service = get_calendar_service()
        calendars = service.calendarList().list().execute()
        calendar_id = "primary"

        for cal in calendars.get('items', []):
            if calendar_name.lower() in cal.get('summary', '').lower():
                calendar_id = cal['id']
                break

        event = create_google_event(calendar_id, "🧪 Prueba Dansu", "2026-07-01T10:00:00+02:00", "2026-07-01T10:30:00+02:00")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=403, detail="Error al verificar calendario")


@app.post("/book-appointment")
async def book_appointment(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        summary = data.get("summary")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        description = data.get("description", "")

        event = create_google_event(calendar_email, summary, start_time, end_time, description)
        return {"status": "success", "message": "Cita agendada correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    return {"status": "Dansu Backend funcionando correctamente"}
