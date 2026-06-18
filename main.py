from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import json
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI()

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
if not RETELL_API_KEY:
    raise Exception("RETELL_API_KEY no encontrada")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials)

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = ""):
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'description': description or "Cita agendada por Dansu",
            'start': {'dateTime': start_time, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': end_time, 'timeZone': 'Europe/Madrid'},
        }
        created = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='all').execute()
        print(f"✅ CITA CREADA EN GOOGLE: {created.get('htmlLink')}")
        return created
    except Exception as e:
        print(f"❌ Error Google: {e}")
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

# ==================== CREAR BOT ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    ahora = datetime.now()
    fecha_actual = ahora.strftime("%A, %d de %B de %Y")

    custom_prompt = f"""Eres el asistente de {nombre_negocio}.

**Hoy es {fecha_actual}**

Eres amable, cercano y profesional.

Cuando el cliente quiera agendar una cita:
- Ve paso a paso.
- Pregunta día y hora, luego motivo, luego teléfono.
- Resume y confirma todo.
- **Solo cuando el cliente confirme**, llama a la herramienta `book_appointment`.

No cambies de tema hasta que la cita esté agendada."""

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4.1-mini",
        "general_prompt": custom_prompt
    })
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error LLM")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES",
        "tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda la cita en Google Calendar",
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
        raise Exception("Error Agent")

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

    return {"status": "success", "phone_number": free_number, "calendar_email": calendar_email}

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
        return resultado

    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/book-appointment")
async def book_appointment(request: Request):
    try:
        data = await request.json()
        print("📨 RECIBIDO DE RETELL:", json.dumps(data, indent=2))

        calendar_email = data.get("calendar_email")
        if not calendar_email:
            raise HTTPException(status_code=400, detail="No llegó calendar_email")

        event = create_google_event(
            calendar_id=calendar_email,
            summary=data.get("summary", "Cita"),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            description=data.get("description", "")
        )

        print("✅ CITA CREADA EN GOOGLE CALENDAR")
        return {"status": "success", "message": "Cita agendada"}

    except Exception as e:
        print(f"❌ Error book-appointment: {e}")
        raise HTTPException(status_code=500, detail="Error al agendar")


@app.get("/")
async def root():
    return {"status": "Dansu listo"}
