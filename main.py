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
    raise Exception("RETELL_API_KEY no encontrada")

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
if not GOOGLE_CREDENTIALS_JSON:
    raise Exception("GOOGLE_CREDENTIALS no encontrada")

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

        service = build('calendar', 'v3', credentials=credentials, cache_discovery=False)
        return service
    except Exception as e:
        print(f"❌ Error creando servicio Google: {e}")
        raise


def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = ""):
    try:
        service = get_calendar_service()
        
        event = {
            'summary': summary[:100],
            'description': (description or "Cita agendada por asistente Dansu") + f"\n\nAgendado vía Dansu AI - {calendar_id}",
            'start': {'dateTime': start_time, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': end_time, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True},
            # ←←← SIN ATTENDEES (para evitar el error 403)
        }
        
        created = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates='none'          # ← Cambiado a 'none' para Service Account
        ).execute()
        
        print(f"✅ EVENTO CREADO CORRECTAMENTE: {created.get('htmlLink')}")
        return created
    except HttpError as e:
        print(f"❌ Google HttpError {e.status_code}: {e.reason}")
        raise
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


# ==================== CREACIÓN DE BOT (sin cambios) ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    ahora = datetime.now()
    fecha_base = ahora.strftime("%A, %d de %B de %Y")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio}...

**Reglas:**
- Ve paso a paso.
- Confirma fecha y hora antes de usar la herramienta book_appointment."""

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "custom_variables": [{"name": "calendar_email", "type": "string", "value": calendar_email}]
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
            "description": "Agenda la cita confirmada en el calendario del negocio.",
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

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}


# ==================== ENDPOINT ULTRA VERBOSO ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    print("=" * 90)
    print("🚨 RETELL AI HA LLAMADO AL ENDPOINT /book-appointment")
    print("=" * 90)

    try:
        body_bytes = await request.body()
        raw_text = body_bytes.decode("utf-8")
        
        print("📥 RAW BODY RECIBIDO:")
        print(raw_text)
        print("-" * 90)

        data = await request.json()
        print("📋 JSON PARSEADO:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print("-" * 90)

        if "args" in data and isinstance(data["args"], dict):
            args = data["args"]
            print("🔍 FORMATO: WRAPPED (con 'args')")
        else:
            args = data
            print("🔍 FORMATO: ARGS ONLY")

        print("🔑 ARGUMENTOS EXTRAÍDOS:")
        print(json.dumps(args, indent=2, ensure_ascii=False))
        print("-" * 90)

        calendar_email = args.get("calendar_email")
        summary        = args.get("summary")
        start_time     = args.get("start_time")
        end_time       = args.get("end_time")
        description    = args.get("description", "")

        if not all([calendar_email, summary, start_time, end_time]):
            print("❌ FALTAN CAMPOS")
            raise HTTPException(status_code=400, detail="Faltan campos obligatorios")

        print(f"✅ Datos válidos → Creando evento para {calendar_email}")
        
        event = create_google_event(calendar_email, summary, start_time, end_time, description)

        print("🎉 EVENTO CREADO CON ÉXITO")
        print("=" * 90)

        return {
            "code": "SUCCESS",
            "message": "Cita agendada correctamente",
            "event_id": event.get("id"),
            "html_link": event.get("htmlLink")
        }

    except Exception as e:
        print(f"❌ ERROR EN /book-appointment: {e}")
        print("=" * 90)
        return {"code": "ERROR", "message": str(e)}


# Resto de endpoints sin cambios
@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        return create_bot_for_client(
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), voice_id, data.get("google_calendar_email")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        create_google_event(
            calendar_id=data.get("calendar_email"),
            summary="🧪 Prueba Dansu",
            start_time="2026-07-01T10:00:00+02:00",
            end_time="2026-07-01T10:30:00+02:00"
        )
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.get("/")
async def root():
    return {"status": "Dansu Backend OK"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
