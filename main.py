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
    raise Exception("❌ RETELL_API_KEY no encontrada en variables de entorno")

# ==================== CORS (para Wix) ====================
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
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info, scopes=SCOPES
        )
        return build('calendar', 'v3', credentials=credentials)
    except Exception as e:
        print(f"❌ Error cargando credenciales Google: {str(e)}")
        raise HTTPException(status_code=500, detail="Error de configuración Google Calendar")

def create_google_event(calendar_email: str, summary: str, start_time: str, end_time: str, description: str = ""):
    try:
        service = get_calendar_service()
        
        event = {
            'summary': summary,
            'description': description or f"Cita agendada vía Dansu - {calendar_email}",
            'start': {'dateTime': start_time, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': end_time, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }

        created_event = service.events().insert(
            calendarId=calendar_email,
            body=event,
            sendUpdates='all'
        ).execute()

        print(f"✅ Evento creado: {created_event.get('htmlLink')}")
        return created_event

    except Exception as e:
        print(f"❌ Error Google Calendar: {str(e)}")
        raise Exception(f"Error creando evento: {str(e)}")

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
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        r = requests.request(method, url, headers=headers, json=json_data)
        print(f"→ {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Error Retell: {str(e)}")
        return None

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email, model="gpt-4.1-mini"):
    print(f"🤖 Creando bot para: {nombre_negocio} | Voz: {voice_id}")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.
Tu objetivo es atender de forma profesional, amable y eficiente.
Información clave:
- Servicios: {servicios}
- Horario: {horario}
- Zona: {zona}

Habla siempre en español natural. Cuando el cliente quiera agendar una cita, confirma los detalles y usa la herramienta 'book_appointment'."""

    # Crear LLM
    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": model,
        "general_prompt": custom_prompt
    })
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM en Retell")

    llm_id = llm_res["llm_id"]

    # Crear Agent
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": "es-ES"
    })
    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent en Retell")

    agent_id = agent_res["agent_id"]

    # Buscar y asignar número libre
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents") or len(p.get("inbound_agents", [])) == 0:
                free_number = p.get("phone_number")
                break

    if free_number:
        retell_request("PATCH", f"/update-phone-number/{free_number}", {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
        })
    else:
        print("⚠️ No se encontró número libre")

    return {
        "status": "success",
        "agent_id": agent_id,
        "phone_number": free_number,
        "calendar_email": calendar_email
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

        resultado = create_bot_for_client(
            nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email
        )
        return resultado

    except Exception as e:
        print(f"❌ Error en /create-retell-bot: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


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
            raise HTTPException(status_code=400, detail="Faltan datos para agendar la cita")

        event = create_google_event(calendar_email, summary, start_time, end_time, description)

        return {
            "status": "success",
            "message": "Cita agendada correctamente",
            "event_link": event.get("htmlLink")
        }
    except Exception as e:
        print(f"❌ Error en /book-appointment: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    return {"status": "Dansu Backend v2 - Google Calendar activado"}
