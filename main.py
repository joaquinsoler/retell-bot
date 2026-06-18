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

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    try:
        credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info, scopes=SCOPES
        )
        credentials = credentials.with_scopes(SCOPES)
        if hasattr(credentials, 'with_subject'):
            credentials = credentials.with_subject(None)
        if hasattr(credentials, '_regional_access_boundary'):
            credentials._regional_access_boundary = None

        return build('calendar', 'v3', credentials=credentials, cache_discovery=False)
    except Exception as e:
        print(f"❌ Error creando servicio Google: {e}")
        raise


def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = ""):
    try:
        service = get_calendar_service()
        
        event = {
            'summary': summary[:100],
            'description': (description or "Cita agendada por asistente Dansu") + "\n\nAgendado vía Dansu AI",
            'start': {'dateTime': start_time, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': end_time, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        
        created = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates='none'
        ).execute()
        
        print(f"✅ EVENTO CREADO CORRECTAMENTE: {created.get('htmlLink')}")
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


def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        print(f"→ Retell {method} {endpoint} → Status: {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Error Retell: {e}")
        return None


# ==================== CREACIÓN DEL BOT ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    ahora = datetime.now()
    fecha_base = ahora.strftime("%A, %d de %B de %Y")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio} ({sector}).

**REGLAS OBLIGATORIAS (nunca las rompas):**
- Para agendar cualquier cita **DEbes** usar la herramienta `book_appointment`.
- Nunca digas que la cita está agendada si no has recibido respuesta "SUCCESS" de la herramienta.
- Pregunta paso a paso: día y hora → confirma la fecha y hora exacta → motivo → nombre y teléfono.
- Solo después de la confirmación del usuario, llama a la herramienta.

Información del negocio:
- Servicios: {servicios}
- Horario: {horario}
- Zona: {zona}
- Fecha actual: {fecha_base}"""

    # Crear LLM con la herramienta incluida
    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda una cita en el calendario del negocio. Úsala SOLO después de confirmar fecha y hora con el usuario.",
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

    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM en Retell AI")

    # Crear Agent
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES"
    })

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent en Retell AI")

    agent_id = agent_res["agent_id"]

    # Asignar número telefónico
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


# ==================== ENDPOINT BOOK APPOINTMENT (Ultra logging) ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    print("=" * 100)
    print("🚨 RETELL AI HA LLAMADO A /book-appointment")
    print("=" * 100)

    try:
        raw_body = (await request.body()).decode("utf-8")
        print("📥 RAW BODY RECIBIDO:\n", raw_body)
        print("-" * 80)

        data = await request.json()
        print("📋 JSON PARSEADO:\n", json.dumps(data, indent=2, ensure_ascii=False))
        print("-" * 80)

        # Soporta ambos formatos de Retell
        args = data.get("args", data)
        print("🔑 ARGUMENTOS EXTRAÍDOS:\n", json.dumps(args, indent=2, ensure_ascii=False))
        print("-" * 80)

        calendar_email = args.get("calendar_email")
        summary = args.get("summary")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        description = args.get("description", "")

        if not all([calendar_email, summary, start_time, end_time]):
            print("❌ FALTAN CAMPOS OBLIGATORIOS")
            raise HTTPException(status_code=400, detail="Faltan campos obligatorios")

        event = create_google_event(calendar_email, summary, start_time, end_time, description)

        print("🎉 EVENTO CREADO CON ÉXITO")
        print("=" * 100)

        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}

    except Exception as e:
        print(f"❌ ERROR EN BOOK-APPOINTMENT: {e}")
        print("=" * 100)
        return {"code": "ERROR", "message": str(e)}


# ==================== OTROS ENDPOINTS ====================
@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)

        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")

        return create_bot_for_client(
            data.get("nombre_negocio"),
            data.get("sector"),
            data.get("servicios"),
            data.get("horario"),
            data.get("zona"),
            voice_id,
            data.get("google_calendar_email")
        )
    except Exception as e:
        print(f"❌ Error en create-retell-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        create_google_event(
            data.get("calendar_email"),
            "🧪 Prueba de conexión - Dansu",
            "2026-07-01T10:00:00+02:00",
            "2026-07-01T10:30:00+02:00"
        )
        return {"status": "success", "message": "Acceso verificado"}
    except Exception as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.get("/")
async def root():
    return {"status": "Dansu Backend funcionando correctamente ✅"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
