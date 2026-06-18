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
        print(f"✅ EVENTO CREADO EXITOSAMENTE: {created.get('htmlLink')}")
        return created
    except Exception as e:
        print(f"❌ Error crítico en Google Calendar: {e}")
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
        print(f"→ Retell API {method} {endpoint} → Status: {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Error de conexión con Retell: {e}")
        return None

# ==================== CREAR BOT ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    
    ahora = datetime.now()
    fecha_base = ahora.strftime("%A, %d de %B de %Y")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.

**Contexto Temporal Crítico:**
- Fecha actual: {fecha_base}. Año 2026.
- Calcula los días basándote estrictamente en esta fecha.

Información de la empresa:
- Servicios: {servicios}
- Horario: {horario}
- Zona: {zona}

**Tu personalidad:** Amable y profesional.

**Reglas para agendar citas:**
- Pregunta una cosa cada vez.
- Confirma los datos antes de agendar.
- Utiliza la herramienta `book_appointment` enviando las fechas en formato ISO 8601 con zona horaria de Madrid (+02:00).
- Tienes el email del calendario guardado internamente en tu parámetro `calendar_email`."""

    # Creamos el LLM con la variable persistente
    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "custom_variables": [
            {
                "name": "calendar_email",
                "type": "string",
                "value": calendar_email
            }
        ]
    })
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM en Retell")

    # CAMBIO IMPORTANTE: Añadimos la barra inclinada al final '/' para evitar 
    # redirecciones 301/302 de Render que disparan el bloqueo de red de Retell.
    book_appointment_url = "https://retell-bot.onrender.com/book-appointment/"

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES",
        "tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda la cita confirmada en el calendario del negocio.",
            "url": book_appointment_url,
            "method": "POST",
            "parameters": {
                "type": "object",
                "properties": {
                    "calendar_email": {"type": "string", "description": "Email del calendario"},
                    "summary": {"type": "string", "description": "Nombre y motivo"},
                    "start_time": {"type": "string", "description": "ISO 8601 con zona horaria de Madrid (ej: +02:00)"},
                    "end_time": {"type": "string", "description": "ISO 8601 con zona horaria de Madrid (ej: +02:00)"},
                    "description": {"type": "string", "description": "Teléfono y notas extra"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time"]
            }
        }]
    })

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent en Retell")

    agent_id = agent_res["agent_id"]

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

# ==================== ENDPOINTS CON SOPORTE DE RUTA DOBLE ====================

# Soportamos tanto con barra como sin barra al final para evitar fallos de precondición HTTP
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    print("🚨 ¡EL ENDPOINT /book-appointment HA SIDO TOCADO POR RETELL!")
    try:
        data = await request.json()
        print("📩 JSON RECIBIDO:", json.dumps(data, indent=2, ensure_ascii=False))

        calendar_email = data.get("calendar_email")
        summary = data.get("summary")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        description = data.get("description", "")

        if not calendar_email:
            raise HTTPException(status_code=400, detail="Falta calendar_email")

        event = create_google_event(calendar_email, summary, start_time, end_time, description)

        return {
            "code": "SUCCESS",
            "message": "Cita agendada correctamente",
            "event_id": event.get("id")
        }

    except Exception as e:
        print(f"❌ Error procesando cita en backend: {e}")
        return {
            "code": "ERROR",
            "message": f"Error interno: {str(e)}"
        }

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload.get("data", payload)
        
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        resultado = create_bot_for_client(
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email")
        )
        return resultado
    except Exception as e:
        print(f"❌ Error create-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        event = create_google_event(
            calendar_id=data.get("calendar_email"),
            summary="🧪 Prueba de conexión - Dansu",
            start_time="2026-07-01T10:00:00+02:00",
            end_time="2026-07-01T10:30:00+02:00"
        )
        return {"status": "success", "message": "Acceso verificado correctamente"}
    except Exception as e:
        raise HTTPException(status_code=403, detail=str(e))

@app.get("/")
async def root():
    return {"status": "Dansu Backend funcionando"}
