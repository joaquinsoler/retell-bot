import os
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import traceback

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Dansu Backend")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON:
    raise Exception("Faltan variables de entorno")

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


def ensure_calendar_access(calendar_id: str):
    try:
        service = get_calendar_service()
        service.calendarList().insert(body={'id': calendar_id}).execute()
        print(f"✅ Calendario suscrito: {calendar_id}")
    except HttpError as e:
        if e.status_code == 409:
            print(f"ℹ️ Ya suscrito: {calendar_id}")
        else:
            print(f"⚠️ Error suscripción {e.status_code}: {e}")


def is_time_slot_available(calendar_id: str, start_time: str, duration_minutes: int = 60):
    try:
        print(f"🔍 Comprobando disponibilidad para {start_time}")
        service = get_calendar_service()
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        buffer_start = (start_dt - timedelta(minutes=60)).isoformat(timespec='seconds') + 'Z'
        end_dt = (start_dt + timedelta(minutes=duration_minutes)).isoformat(timespec='seconds') + 'Z'

        body = {
            "timeMin": buffer_start,
            "timeMax": end_dt,
            "items": [{"id": calendar_id}]
        }

        freebusy = service.freebusy().query(body=body).execute()
        busy_slots = freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])

        if busy_slots:
            print(f"❌ HORARIO NO DISPONIBLE: {busy_slots}")
            return False
        return True
    except Exception as e:
        print(f"⚠️ Error en disponibilidad: {e}")
        return True


def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", check_availability=True):
    try:
        ensure_calendar_access(calendar_id)
        if check_availability and not is_time_slot_available(calendar_id, start_time):
            raise Exception("Horario no disponible (buffer de 1 hora)")

        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': (description or "Cita agendada por Dansu AI"),
            'start': {'dateTime': start_time, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': end_time, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }

        created = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates='none'
        ).execute()
        print(f"✅ EVENTO CREADO: {created.get('htmlLink')}")
        return created
    except Exception as e:
        print(f"❌ Error create_google_event: {e}")
        raise


def cancel_google_event(calendar_id: str, start_time: str, summary: str = None):
    try:
        ensure_calendar_access(calendar_id)
        service = get_calendar_service()

        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        time_min = (start_dt - timedelta(minutes=30)).isoformat(timespec='seconds') + 'Z'
        time_max = (start_dt + timedelta(minutes=30)).isoformat(timespec='seconds') + 'Z'

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        if not events:
            raise Exception("No se encontró cita para cancelar en ese horario")

        # Cancelar la primera coincidencia
        event_id = events[0]['id']
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        print(f"✅ CITA CANCELADA: {events[0].get('summary')}")
        return {"status": "success"}
    except Exception as e:
        print(f"❌ Error cancelando cita: {e}")
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


# ==================== CREACIÓN DEL BOT ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    ahora = datetime.now()
    fecha_base = ahora.strftime("%A, %d de %B de %Y")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio} ({sector}).

**INFORMACIÓN CRÍTICA:**
- Email del calendario: {calendar_email}

**Herramientas:**
- book_appointment → Agendar cita
- cancel_appointment → Cancelar cita existente

**Flujo agendar:** Pregunta uno por uno (nombre, teléfono, motivo) y luego llama a la herramienta.
**Flujo cancelar:** Confirma fecha y hora y llama a cancel_appointment."""

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [
            {
                "type": "custom",
                "name": "book_appointment",
                "description": "Agenda una nueva cita.",
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
            },
            {
                "type": "custom",
                "name": "cancel_appointment",
                "description": "Cancela una cita existente. Requiere calendar_email y start_time.",
                "url": "https://retell-bot.onrender.com/cancel-appointment",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "calendar_email": {"type": "string"},
                        "start_time": {"type": "string"},
                        "summary": {"type": "string"}
                    },
                    "required": ["calendar_email", "start_time"]
                }
            }
        ]
    })

    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES"
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


# ==================== ENDPOINTS ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    print("=" * 120)
    print("🚨 RETELL LLAMÓ A /book-appointment")
    print("=" * 120)
    try:
        raw = (await request.body()).decode("utf-8")
        print("RAW BODY:\n", raw)

        data = await request.json()
        args = data.get("args", data)

        print("ARGUMENTOS RECIBIDOS:\n", json.dumps(args, indent=2, ensure_ascii=False))

        event = create_google_event(
            args.get("calendar_email"),
            args.get("summary"),
            args.get("start_time"),
            args.get("end_time"),
            args.get("description", ""),
            check_availability=True
        )

        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}
    except Exception as e:
        print(f"❌ ERROR EN BOOK-APPOINTMENT: {e}")
        print(traceback.format_exc())
        return {"code": "ERROR", "message": str(e)}


@app.post("/cancel-appointment")
@app.post("/cancel-appointment/")
async def cancel_appointment(request: Request):
    print("=" * 120)
    print("🚨 RETELL LLAMÓ A /cancel-appointment")
    print("=" * 120)
    try:
        raw = (await request.body()).decode("utf-8")
        print("RAW BODY:\n", raw)

        data = await request.json()
        args = data.get("args", data)

        print("ARGUMENTOS RECIBIDOS:\n", json.dumps(args, indent=2, ensure_ascii=False))

        result = cancel_google_event(
            args.get("calendar_email"),
            args.get("start_time"),
            args.get("summary")
        )

        return {"code": "SUCCESS", "message": "Cita cancelada correctamente"}
    except Exception as e:
        print(f"❌ ERROR EN CANCEL-APPOINTMENT: {e}")
        print(traceback.format_exc())
        return {"code": "ERROR", "message": str(e)}


@app.post("/verify-calendar-access")
@app.post("/verify-calendar-access/")
async def verify_calendar_access(request: Request):
    print("=" * 80)
    print("🔍 VERIFICANDO ACCESO A GOOGLE CALENDAR")
    print("=" * 80)
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        print(f"Email recibido: {calendar_email}")

        create_google_event(
            calendar_email,
            "🧪 Prueba de conexión - Dansu",
            "2026-07-01T10:00:00+02:00",
            "2026-07-01T10:30:00+02:00",
            check_availability=False
        )
        return {"status": "success", "message": "Acceso verificado correctamente"}
    except Exception as e:
        print(f"❌ Error en verify: {e}")
        raise HTTPException(status_code=400, detail=str(e))


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
        print(f"❌ Error en create-retell-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    return {"status": "Dansu Backend OK - Cancelación añadida"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
