import subprocess
import sys

# Instalador automático de dependencias en Render
REQUIRED_PACKAGES = ["fastapi", "uvicorn", "requests", "google-auth", "google-api-python-client"]
for package in REQUIRED_PACKAGES:
    try:
        if package == "google-api-python-client": import googleapiclient
        else: __import__(package.replace("-", "_"))
    except ImportError:
        print(f"📦 Instalando dependencia: {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import requests
import json
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
RENDER_SERVER_URL = os.getenv("RENDER_SERVER_URL")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

VOICE_MAPPING = {
    "Cimo": "11labs-Adrian", "Brynne": "11labs-Brynne", "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova", "Grace": "openai-Shimmer", "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa", "Lily": "11labs-Lily", "Della": "11labs-Delia",
    "Nico": "openai-Onyx", "Rita": "11labs-Rita", "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa", "Maren": "11labs-Maren", "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley", "Andrea": "openai-Alloy", "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby", "Alejandro": "openai-Echo", "Sloane": "11labs-Sloane"
}

def get_calendar_service():
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        raise Exception("Falta la variable GOOGLE_SERVICE_ACCOUNT_JSON en Render.")
    
    if GOOGLE_SERVICE_ACCOUNT_JSON.startswith("{"):
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/calendar']
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_JSON, scopes=['https://www.googleapis.com/auth/calendar']
        )
    return build('calendar', 'v3', credentials=creds)

def retell_request(method, endpoint, json_data=None):
    if not RETELL_API_KEY: return None
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data)
        return r.json() if r.ok else None
    except Exception:
        return None

# =====================================================================
# PASO 1: WEBHOOK DE REGISTRO COMERCIAL Y ALTA DE BOT CON METADATOS
# =====================================================================
@app.post("/create-retell-bot")
async def wix_webhook(request: Request):
    try:
        payload = await request.json()
        data = payload.get("data", payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Payload corrupto o vacío.")

    calendar_id = data.get("calendar_id")
    if not calendar_id:
        raise HTTPException(status_code=400, detail="Falta el campo 'calendar_id'.")

    nombre_negocio = data.get("nombre_negocio", "Mi Negocio AI")
    sector = data.get("sector", "General")
    servicios = data.get("servicios", "Consultas")
    horario = data.get("horario", "Horario comercial")
    zona = data.get("zona", "España")
    voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")

    # 1. Registrar Custom Tool en Retell
    tool_definition = {
        "tool_name": f"agenda_{nombre_negocio.lower().replace(' ', '_')}",
        "tool_type": "custom",
        "url": f"{RENDER_SERVER_URL}/retell-check-and-book",
        "method": "POST",
        "description": "Comprueba la disponibilidad de citas o guarda reservas en la agenda de Google Calendar de la empresa.",
        "parameters": {
            "type": "object",
            "properties": {
                "accion": {"type": "string", "description": "Acción a realizar: 'comprobar' o 'reservar'."},
                "fecha_hora": {"type": "string", "description": "Formato ISO 8601 (YYYY-MM-DDTHH:MM:SS)."},
                "nombre_paciente": {"type": "string", "description": "Nombre completo del cliente (obligatorio solo para reservar)."}
            },
            "required": ["accion", "fecha_hora"]
        }
    }
    tool_res = retell_request("POST", "/create-tool", tool_definition)
    tool_name = tool_res.get("tool_name") if tool_res else None

    # 2. Configurar el Prompt y el LLM
    custom_prompt = (
        f"Eres el asistente virtual telefónico de {nombre_negocio}, especializado en el sector {sector}.\n"
        f"Servicios disponibles: {servicios}\nHorario de atención: {horario}\nUbicación: {zona}\n\n"
        f"Tu único objetivo es gestionar citas usando la herramienta de calendario asignada. "
        f"Antes de confirmar cualquier reserva, comprueba si el hueco está libre. Responde de forma muy natural, corta y siempre en español."
    )
    llm_payload = {"model": "gpt-4o-mini", "general_prompt": custom_prompt}
    if tool_name: llm_payload["tools"] = [tool_name]
    
    llm_res = retell_request("POST", "/create-retell-llm", llm_payload)
    if not llm_res or "llm_id" not in llm_res:
        raise HTTPException(status_code=500, detail="Fallo al registrar el motor LLM en Retell.")
    llm_id = llm_res["llm_id"]

    # 3. Crear el Agente vinculando el ID de calendario en sus metadatos internos
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": "es-ES",
        # Inyección persistente de metadatos para evitar pérdidas por reinicio de Render
        "metadata": {"linked_calendar_id": calendar_id}
    })
    
    if not agent_res or "agent_id" not in agent_res:
        raise HTTPException(status_code=500, detail="Fallo al registrar el agente en Retell.")
    agent_id = agent_res["agent_id"]

    # 4. Asignar número telefónico libre
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = "+34 (Asignando número...)"
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents") or len(p["inbound_agents"]) == 0:
                free_number = p.get("phone_number")
                break
    
    if free_number != "+34 (Asignando número...)":
        retell_request("PATCH", f"/update-phone-number/{free_number}", {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
        })

    return {
        "status": "success",
        "agent_id": agent_id,
        "phone_number": free_number,
        "calendar_id": calendar_id
    }


# =====================================================================
# VERIFICACIÓN DE PERMISOS DE GOOGLE CALENDAR
# =====================================================================
@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        payload = await request.json()
        calendar_id = payload.get("calendar_id")
        if not calendar_id: raise HTTPException(status_code=400, detail="Falta el campo 'calendar_id'.")
        
        service = get_calendar_service()
        service.calendars().get(calendarId=calendar_id).execute()
        return {"status": "authorized", "message": "Acceso verificado con éxito."}
    except Exception:
        raise HTTPException(status_code=403, detail="Acceso denegado por Google Calendar.")


# =====================================================================
# INTERACCIÓN PERSISTENTE EN TIEMPO REAL DURANTE LA LLAMADA TELEFÓNICA
# =====================================================================
@app.post("/retell-check-and-book")
async def retell_interaction(request: Request):
    try:
        payload = await request.json()
        agent_id = payload.get("agent_id")
        args = payload.get("args", {})
        
        action = args.get("accion")
        fecha_hora_str = args.get("fecha_hora")
        nombre_paciente = args.get("nombre_paciente", "Paciente Anónimo")

        # 🧠 EXTRACCIÓN PERSISTENTE NATIVA: Si la memoria del servidor se borró, le pedimos los metadatos directos a Retell
        calendar_id = None
        print(f"📞 Interacción telefónica entrante del agente: {agent_id}")
        
        agent_profile = retell_request("GET", f"/get-agent/{agent_id}")
        if agent_profile and "metadata" in agent_profile:
            calendar_id = agent_profile["metadata"].get("linked_calendar_id")
            
        if not calendar_id:
            print("❌ No se ha podido localizar el metadato 'linked_calendar_id' en Retell.")
            return {"status": "error", "mensaje": "Este agente no tiene metadatos de agenda configurados."}

        service = get_calendar_service()

        base_time = fecha_hora_str.replace("Z", "").split(".")[0]
        start_time = datetime.fromisoformat(base_time)
        end_time = start_time + timedelta(minutes=30)

        time_min = start_time.strftime("%Y-%m-%dT%H:%M:%S+02:00")
        time_max = end_time.strftime("%Y-%m-%dT%H:%M:%S+02:00")

        # Consultar huecos libres en Google Calendar
        events_result = service.events().list(
            calendarId=calendar_id, timeMin=time_min, timeMax=time_max, singleEvents=True
        ).execute()
        is_occupied = len(events_result.get('items', [])) > 0

        if action == "comprobar":
            return {"status": "ocupado" if is_occupied else "libre"}
            
        elif action == "reservar":
            if is_occupied:
                return {"status": "error", "mensaje": "El hueco se ha ocupado."}
                
            event_body = {
                'summary': f"Cita AI: {nombre_paciente}",
                'description': 'Cita agendada de forma automática por el Asistente de Voz AI.',
                'start': {'dateTime': time_min, 'timeZone': 'Europe/Madrid'},
                'end': {'dateTime': time_max, 'timeZone': 'Europe/Madrid'},
            }
            service.events().insert(calendarId=calendar_id, body=event_body).execute()
            print(f"📅 ¡Cita guardada con éxito en la agenda: {calendar_id}!")
            return {"status": "reservado", "mensaje": "Cita registrada con éxito."}
            
    except Exception as e:
        print(f"❌ Error crítico procesando la herramienta de agenda: {str(e)}")
        return {"status": "error", "mensaje": str(e)}
