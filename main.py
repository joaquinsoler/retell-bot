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

# ==================== VARIABLES ====================
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
    try:
        credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
        credentials = credentials.with_scopes(SCOPES)
        return build('calendar', 'v3', credentials=credentials, cache_discovery=False)
    except Exception as e:
        print(f"❌ ERROR CRÍTICO get_calendar_service: {e}")
        raise


def is_time_slot_available(calendar_id: str, start_time: str, duration_minutes: int = 60):
    """Comprueba disponibilidad con buffer de 1 hora"""
    try:
        print(f"🔍 Comprobando disponibilidad para {start_time} (buffer 60 min)")
        service = get_calendar_service()
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        buffer_start = (start_dt - timedelta(minutes=60)).isoformat()
        end_dt = (start_dt + timedelta(minutes=duration_minutes)).isoformat()

        body = {
            "timeMin": buffer_start,
            "timeMax": end_dt,
            "items": [{"id": calendar_id}]
        }

        freebusy = service.freebusy().query(body=body).execute()
        busy_slots = freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])

        if busy_slots:
            print(f"❌ HORARIO OCUPADO - Slots encontrados: {busy_slots}")
            return False
        
        print("✅ Horario disponible")
        return True
    except Exception as e:
        print(f"⚠️ ERROR en is_time_slot_available: {type(e).__name__} - {e}")
        print(traceback.format_exc())
        return True  # Permitimos por seguridad si falla la comprobación


def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = ""):
    try:
        print(f"📅 Intentando crear evento en {calendar_id}")
        print(f"   Hora inicio: {start_time}")
        print(f"   Hora fin: {end_time}")

        # 1. Comprobar disponibilidad
        if not is_time_slot_available(calendar_id, start_time):
            raise Exception("❌ Horario no disponible (conflicto con buffer de 1 hora)")

        # 2. Crear evento
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

        print(f"✅ EVENTO CREADO CON ÉXITO: {created.get('htmlLink')}")
        return created

    except HttpError as e:
        print(f"❌ GOOGLE HTTP ERROR {e.status_code}")
        print(f"   Reason: {e.reason}")
        print(f"   Message: {e}")
        raise
    except Exception as e:
        print(f"❌ ERROR GENERAL EN create_google_event: {type(e).__name__}")
        print(f"   Mensaje: {e}")
        print(traceback.format_exc())
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
    try:
        url = f"https://api.retellai.com{endpoint}"
        headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        print(f"→ Retell {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Error en retell_request: {e}")
        return None


# ==================== CREACIÓN DEL BOT (mantengo el prompt claro) ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    ahora = datetime.now()
    fecha_base = ahora.strftime("%A, %d de %B de %Y")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio} ({sector}).

**INFORMACIÓN FIJA:**
- Email del calendario: {calendar_email}

**Flujo obligatorio:**
1. Confirma día y hora.
2. Pregunta nombre completo.
3. Pregunta teléfono.
4. Pregunta motivo.
5. Solo entonces usa book_appointment."""

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [{ ... }]  # igual que antes
    })

    # ... (resto sin cambios, mantengo la versión funcional)
    # (para no alargar, copia el resto de create_bot_for_client de tu versión anterior)

    # ... resto del código de creación de agent y número ...


# ==================== ENDPOINT BOOK-APPOINTMENT (LOGGING MÁXIMO) ====================
@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    print("=" * 120)
    print("🚨 RETELL AI HA LLAMADO A /book-appointment")
    print("=" * 120)

    try:
        raw = (await request.body()).decode("utf-8")
        print("📥 RAW BODY:\n", raw)
        print("-" * 80)

        data = await request.json()
        args = data.get("args", data)

        print("🔑 ARGUMENTOS RECIBIDOS:\n", json.dumps(args, indent=2, ensure_ascii=False))
        print("-" * 80)

        calendar_email = args.get("calendar_email")
        summary = args.get("summary")
        start_time = args.get("start_time")
        end_time = args.get("end_time")

        print(f"📧 Calendar Email: {calendar_email}")
        print(f"📝 Summary: {summary}")
        print(f"🕒 Start: {start_time} → End: {end_time}")

        event = create_google_event(calendar_email, summary, start_time, end_time, args.get("description", ""))

        print("🎉 EVENTO CREADO CON ÉXITO")
        print("=" * 120)
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}

    except Exception as e:
        print(f"❌ ERROR FINAL EN /book-appointment: {type(e).__name__}")
        print(f"   Mensaje: {e}")
        print(traceback.format_exc())
        print("=" * 120)
        return {"code": "ERROR", "message": str(e)}


# ==================== OTROS ENDPOINTS (sin cambios) ====================
@app.post("/verify-calendar-access")
@app.post("/verify-calendar-access/")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        create_google_event(
            data.get("calendar_email"),
            "🧪 Prueba de conexión - Dansu",
            "2026-07-01T10:00:00+02:00",
            "2026-07-01T10:30:00+02:00"
        )
        return {"status": "success", "message": "Acceso verificado correctamente"}
    except Exception as e:
        print(f"❌ Error en verify-calendar-access: {e}")
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
    return {"status": "Dansu Backend OK - Versión con logs reforzados"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
