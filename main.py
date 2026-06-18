from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI()

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
if not RETELL_API_KEY:
    raise Exception("RETELL_API_KEY no encontrada en variables de entorno")

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
    try:
        credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
        credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
        return build('calendar', 'v3', credentials=credentials)
    except Exception as e:
        print(f"❌ Error credenciales Google: {e}")
        raise

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = ""):
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_time, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': end_time, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        return service.events().insert(calendarId=calendar_id, body=event, sendUpdates='all').execute()
    except Exception as e:
        print(f"❌ Error creando evento: {e}")
        raise

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

# ==================== CREAR BOT CON TOOL ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    custom_prompt = f"""Eres el asistente virtual profesional de {nombre_negocio}, una empresa del sector {sector}.

Información clave:
- Servicios: {servicios}
- Horario: {horario}
- Zona de servicio: {zona}

**Instrucciones importantes:**
- Habla siempre en español de forma natural, amable y profesional.
- Cuando el cliente quiera agendar una cita, pregunta: fecha aproximada, hora, motivo y número de teléfono de contacto.
- Confirma los datos con el cliente.
- Una vez confirmados, usa la herramienta `book_appointment` para agendar la cita.
- Nunca inventes horarios. Propón opciones si es necesario."""

    # Crear LLM
    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4.1-mini",
        "general_prompt": custom_prompt
    })
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")

    llm_id = llm_res["llm_id"]

    # Crear Agent con Tool para agendar citas
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": "es-ES",
        "tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda una cita en el calendario de Google del negocio",
            "url": "https://retell-bot.onrender.com/book-appointment",
            "method": "POST",
            "parameters": {
                "type": "object",
                "properties": {
                    "calendar_email": {"type": "string", "description": "Email del calendario del negocio"},
                    "summary": {"type": "string", "description": "Título de la cita (ej: Corte de pelo - Juan Pérez)"},
                    "start_time": {"type": "string", "description": "Inicio en formato ISO (ej: 2026-07-05T10:00:00+02:00)"},
                    "end_time": {"type": "string", "description": "Fin en formato ISO"},
                    "description": {"type": "string", "description": "Motivo + teléfono del cliente"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time"]
            }
        }]
    })

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent")

    agent_id = agent_res["agent_id"]

    # Asignar número de teléfono
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
            raise HTTPException(status_code=422, detail="Faltan campos obligatorios")

        voice_id = VOICE_MAPPING.get(asistente, "openai-Alloy")

        resultado = create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email)
        resultado["calendar_email"] = calendar_email
        return resultado

    except Exception as e:
        print(f"❌ Error create-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        calendar_name = data.get("calendar_name", "").strip() or "primary"

        service = get_calendar_service()
        calendars = service.calendarList().list().execute()
        calendar_id = "primary"

        for cal in calendars.get('items', []):
            if calendar_name.lower() in cal.get('summary', '').lower() or cal.get('id') == calendar_email:
                calendar_id = cal['id']
                print(f"✅ Calendario encontrado: {cal.get('summary')} ({calendar_id})")
                break

        event = create_google_event(
            calendar_id=calendar_id,
            summary="🧪 Prueba de conexión - Dansu",
            start_time="2026-07-01T10:00:00+02:00",
            end_time="2026-07-01T10:30:00+02:00",
            description=f"Prueba Dansu - {calendar_name}"
        )

        return {"status": "success", "message": "Conexión verificada correctamente"}

    except Exception as e:
        print(f"❌ Error verify: {e}")
        raise HTTPException(status_code=403, detail="No se pudo acceder al calendario. Revisa nombre y permisos.")


@app.post("/book-appointment")
async def book_appointment(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        summary = data.get("summary")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        description = data.get("description", "")

        if not all([calendar_email, summary, start_time, end_time]):
            raise HTTPException(status_code=400, detail="Faltan datos para agendar")

        event = create_google_event(calendar_email, summary, start_time, end_time, description)

        return {
            "status": "success",
            "message": "Cita agendada correctamente en Google Calendar"
        }
    except Exception as e:
        print(f"❌ Error booking: {e}")
        raise HTTPException(status_code=500, detail="Error al agendar la cita")


@app.get("/")
async def root():
    return {"status": "Dansu Backend v3 - Agendamiento activo"}
