import os
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Dansu Backend")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
if not RETELL_API_KEY:
    raise Exception("RETELL_API_KEY no encontrada en variables de entorno")

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
if not GOOGLE_CREDENTIALS_JSON:
    raise Exception("GOOGLE_CREDENTIALS no encontrada en variables de entorno")

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== GOOGLE CALENDAR (FIX RAB) ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    """Crea el servicio de Google Calendar con fix para Regional Access Boundary"""
    try:
        credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=SCOPES
        )
        
        # FIX PRINCIPAL para Regional Access Boundary (RAB) error
        # Forzamos scopes y evitamos impersonation / RAB conflictivo
        credentials = credentials.with_scopes(SCOPES)
        
        # Si tu service account no necesita impersonation (domain-wide delegation),
        # aseguramos que no se active subject
        if hasattr(credentials, 'with_subject'):
            credentials = credentials.with_subject(None)
        
        service = build('calendar', 'v3', credentials=credentials)
        return service
    except Exception as e:
        print(f"❌ Error creando servicio de Google Calendar: {e}")
        raise


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
        
        created = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates='all'
        ).execute()
        
        print(f"✅ EVENTO CREADO EN GOOGLE CALENDAR: {created.get('htmlLink')}")
        return created
    except HttpError as e:
        print(f"❌ Google Calendar HttpError: {e.status_code} - {e.reason}")
        raise HTTPException(status_code=400, detail=f"Error de Google Calendar: {e.reason}")
    except Exception as e:
        print(f"❌ Error al insertar evento: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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

def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        print(f"→ Retell API {method} {endpoint} → Status: {r.status_code}")
        if r.ok:
            return r.json()
        else:
            print(f"❌ Retell error body: {r.text}")
            return None
    except Exception as e:
        print(f"❌ Error Retell AI: {e}")
        return None


# ==================== CREACIÓN DE BOT ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    ahora = datetime.now()
    fecha_base = ahora.strftime("%A, %d de %B de %Y")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.

**Contexto Temporal Crítico:**
- Fecha de hoy en el sistema: {fecha_base}. Estamos en el año 2026.
- Determina siempre el día de la cita basándote estrictamente en esta fecha actual.

Información de la empresa:
- Servicios: {servicios}
- Horario: {horario}
- Zona: {zona}

**Tu personalidad:** Amable, cercano y profesional.

**Reglas para agendar citas:**
- Ve paso a paso. Pregunta una cosa cada vez (día/hora, motivo de la cita, nombre y teléfono).
- Confirma la fecha y hora exacta de forma explícita antes de usar la herramienta.
- Tienes el email del calendario guardado internamente, no se lo pidas al usuario.
- Llama a `book_appointment` enviando las fechas en formato ISO 8601 con zona horaria de Madrid (+02:00)."""

    # Crear LLM
    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "custom_variables": [{
            "name": "calendar_email",
            "type": "string",
            "value": calendar_email
        }]
    })
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM en Retell AI")

    book_appointment_url = "https://retell-bot.onrender.com/book-appointment"

    # Crear Agent
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
        raise Exception("Error creando Agente en Retell AI")

    agent_id = agent_res["agent_id"]

    # Asignar número telefónico libre
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

@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    print("🚨 [LOG] Endpoint /book-appointment llamado por Retell")
    try:
        data = await request.json()
        print("📩 Datos recibidos:", json.dumps(data, indent=2, ensure_ascii=False))

        calendar_email = data.get("calendar_email")
        summary = data.get("summary")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        description = data.get("description", "")

        if not all([calendar_email, summary, start_time, end_time]):
            raise HTTPException(status_code=400, detail="Faltan campos obligatorios")

        event = create_google_event(calendar_email, summary, start_time, end_time, description)

        return {
            "code": "SUCCESS",
            "message": "Cita agendada correctamente",
            "event_id": event.get("id")
        }
    except Exception as e:
        print(f"❌ Error en book-appointment: {e}")
        return {"code": "ERROR", "message": str(e)}


@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)

        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")

        resultado = create_bot_for_client(
            data.get("nombre_negocio"),
            data.get("sector"),
            data.get("servicios"),
            data.get("horario"),
            data.get("zona"),
            voice_id,
            data.get("google_calendar_email")
        )
        return resultado
    except Exception as e:
        print(f"❌ Error en create-retell-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        if not calendar_email:
            raise HTTPException(status_code=400, detail="calendar_email requerido")

        # Prueba creando un evento de test
        create_google_event(
            calendar_id=calendar_email,
            summary="🧪 Prueba de conexión - Dansu",
            start_time="2026-07-01T10:00:00+02:00",
            end_time="2026-07-01T10:30:00+02:00"
        )
        return {"status": "success", "message": "Acceso verificado correctamente"}
    except Exception as e:
        print(f"❌ Error verify-calendar-access: {e}")
        raise HTTPException(status_code=403, detail=str(e))


@app.get("/")
async def root():
    return {"status": "Dansu Backend funcionando correctamente ✅"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
