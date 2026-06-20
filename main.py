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
        print(f"🔍 Comprobando disponibilidad para {start_time} (buffer 60 min)")
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
            print(f"❌ HORARIO NO DISPONIBLE - Slots ocupados: {busy_slots}")
            return False

        print("✅ Horario disponible")
        return True

    except Exception as e:
        print(f"⚠️ Error en is_time_slot_available: {e}")
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
        print(f"❌ Error en create_google_event: {e}")
        raise


def cancel_google_event(calendar_id: str, start_time: str, summary: str = None):
    """Cancela una cita buscando por hora (y opcionalmente por resumen)"""
    try:
        ensure_calendar_access(calendar_id)
        service = get_calendar_service()

        # Buscar eventos en una ventana de ±30 minutos
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
            print("❌ No se encontró ninguna cita en ese horario")
            raise Exception("No se encontró ninguna cita para cancelar en ese horario")

        # Si hay varias, intentamos coincidir por summary
        for event in events:
            if summary and summary.lower() in event.get('summary', '').lower():
                service.events().delete(calendarId=calendar_id, eventId=event['id']).execute()
                print(f"✅ Cita cancelada: {event.get('summary')} - {event.get('start')}")
                return {"status": "success", "message": "Cita cancelada correctamente"}
        
        # Si no coincidió por summary, cancelamos la primera encontrada
        service.events().delete(calendarId=calendar_id, eventId=events[0]['id']).execute()
        print(f"✅ Cita cancelada (primera encontrada): {events[0].get('summary')}")
        return {"status": "success", "message": "Cita cancelada correctamente"}

    except Exception as e:
        print(f"❌ Error al cancelar cita: {e}")
        raise


# ==================== VOICE MAPPING ====================
VOICE_MAPPING = { ... }  # (tu diccionario completo)


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


# ==================== CREACIÓN DEL BOT (con tool de cancelar) ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    ahora = datetime.now()
    fecha_base = ahora.strftime("%A, %d de %B de %Y")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio} ({sector}).

**INFORMACIÓN CRÍTICA:**
- Email del calendario: {calendar_email}

**Herramientas disponibles:**
- `book_appointment`: Para agendar nueva cita.
- `cancel_appointment`: Para cancelar una cita existente.

**Flujo para cancelar:**
- Confirma con el usuario la fecha y hora de la cita a cancelar.
- Llama a `cancel_appointment` con los datos correctos.

**Flujo normal para agendar (uno por uno):**
1. Confirma día y hora.
2. Pregunta nombre.
3. Pregunta teléfono.
4. Pregunta motivo.
5. Llama a `book_appointment`."""

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
                "parameters": { ... }  # igual que antes
            },
            {
                "type": "custom",
                "name": "cancel_appointment",
                "description": "Cancela una cita existente. Usa solo cuando el usuario lo pida explícitamente.",
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

    # ... (el resto de la función create_bot_for_client se mantiene exactamente igual que en tu código)
    # (creación de agent, asignación de número, etc.)

    # (para no hacer el mensaje eterno, el resto es idéntico a tu versión original)

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}


# ==================== NUEVO ENDPOINT PARA CANCELAR ====================
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


# ==================== EL RESTO DE ENDPOINTS (sin cambios) ====================
# (book-appointment, verify-calendar-access, create-retell-bot, root) se mantienen exactamente como en tu código original

# ... (pega aquí el resto de tus endpoints que ya tenías)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
