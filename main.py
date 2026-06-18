from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import json
from datetime import datetime
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
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials)

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = ""):
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'description': description or "Cita agendada por asistente Dansu",
            'start': {'dateTime': start_time, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': end_time, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        created = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='all').execute()
        print(f"✅ EVENTO CREADO: {created.get('htmlLink')}")
        return created
    except Exception as e:
        print(f"❌ Error Google Calendar: {e}")
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
    
    # Hemos quitado la fecha/hora congeladas estáticas. Dejamos que el LLM maneje el tiempo de forma fluida.
    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.

Información de la empresa:
- Servicios: {servicios}
- Horario: {horario}
- Zona: {zona}

**Tu personalidad:** Amable, cercano, agradable y profesional. Hablas con calidez y buena actitud.

**Reglas para agendar citas:**
- Ve paso a paso de forma natural.
- Pregunta una cosa cada vez (día/hora, motivo de la cita, nombre y teléfono).
- Confirma la fecha y hora exacta con el usuario antes de registrarla.
- Solo usa la herramienta `book_appointment` cuando todo esté explícitamente confirmado por el usuario.
- No le pidas nunca la dirección de correo electrónico al usuario."""

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt
    })
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")

    # SOLUCIÓN CLAVE: Pasamos el email del calendario en la misma URL que va a consumir la Custom Tool de Retell
    book_appointment_url = f"https://retell-bot.onrender.com/book-appointment?calendar_email={calendar_email}"

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES",
        "tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda la cita confirmada en el calendario de Google del negocio.",
            "url": book_appointment_url,
            "method": "POST",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Nombre del cliente y motivo de la cita (Ej: Juan Pérez - Corte de pelo)"},
                    "start_time": {"type": "string", "description": "Fecha y hora de inicio en formato ISO 8601 (Ej: 2026-07-01T10:00:00+02:00)"},
                    "end_time": {"type": "string", "description": "Fecha y hora de fin en formato ISO 8601, típicamente de 30 a 60 min después del inicio"},
                    "description": {"type": "string", "description": "Detalles adicionales aportados en la llamada como el teléfono de contacto"}
                },
                "required": ["summary", "start_time", "end_time"]
            }
        }]
    })

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent")

    agent_id = agent_res["agent_id"]

    # Asignar número libre de Retell
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
async def create_retell_bot_endpoint(request: Request):
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

        if not calendar_email:
            raise HTTPException(status_code=400, detail="Falta calendar_email")

        event = create_google_event(
            calendar_id=calendar_email,
            summary="🧪 Prueba de conexión - Dansu",
            start_time="2026-07-01T10:00:00+02:00",
            end_time="2026-07-01T10:30:00+02:00"
        )

        return {"status": "success", "message": "Acceso verificado correctamente"}

    except Exception as e:
        print(f"❌ Verify error: {e}")
        raise HTTPException(status_code=403, detail="No se pudo acceder al calendario. Revisa los permisos.")


@app.post("/book-appointment")
async def book_appointment(request: Request, calendar_email: str = None):
    try:
        # FastAPI captura automáticamente 'calendar_email' desde los query parameters de la URL
        data = await request.json()
        print("📩 DATOS RECIBIDOS DE RETELL:", json.dumps(data, indent=2, ensure_ascii=False))

        if not calendar_email:
            calendar_email = data.get("calendar_email")
            
        summary = data.get("summary")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        description = data.get("description", "")

        if not calendar_email:
            raise HTTPException(status_code=400, detail="Falta calendar_email")

        event = create_google_event(calendar_email, summary, start_time, end_time, description)

        return {"status": "success", "message": "Cita agendada correctamente"}

    except Exception as e:
        print(f"❌ Error book-appointment: {e}")
        raise HTTPException(status_code=500, detail="Error al agendar la cita")


@app.get("/")
async def root():
    return {"status": "Dansu Backend funcionando"}
