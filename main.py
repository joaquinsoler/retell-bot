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
GOOGLE_JSON_STR = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
RENDER_SERVER_URL = os.getenv("RENDER_SERVER_URL")

# Base de datos en memoria para asociar agentes con sus calendarios sin depender de la IA
CALENDAR_MAPPING = {}

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
    scopes = ['https://www.googleapis.com/auth/calendar']
    service_account_info = json.loads(GOOGLE_JSON_STR)
    credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=scopes)
    return build('calendar', 'v3', credentials=credentials)

def retell_request(method, endpoint, json_data=None):
    if not RETELL_API_KEY:
        return None
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data)
        return r.json() if r.ok else None
    except Exception as e:
        print(f"⚠️ Error Retell API ({endpoint}): {str(e)}")
        return None

def create_bot_automanaged(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_id):
    # 1. Crear Custom Tool en Retell de forma automática
    tool_definition = {
        "tool_name": f"agenda_{nombre_negocio.lower().replace(' ', '_')}",
        "tool_type": "custom",
        "url": f"{RENDER_SERVER_URL}/retell-check-and-book",
        "method": "POST",
        "description": "Utiliza esta herramienta para comprobar la disponibilidad de citas o para confirmar una reserva en la agenda.",
        "parameters": {
            "type": "object",
            "properties": {
                "accion": {"type": "string", "description": "Determina la acción: 'comprobar' para mirar huecos o 'reservar' para agendar."},
                "fecha_hora": {"type": "string", "description": "Fecha y hora deseada en formato ISO 8601 (YYYY-MM-DDTHH:MM:SS)."},
                "nombre_paciente": {"type": "string", "description": "Nombre completo del cliente (Obligatorio solo para reservar)."}
            },
            "required": ["accion", "fecha_hora"]
        }
    }
    
    tool_res = retell_request("POST", "/create-tool", tool_definition)
    tool_name = tool_res.get("tool_name") if tool_res else None

    # 2. Configurar el prompt maestro del LLM
    custom_prompt = (
        f"Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.\n"
        f"Servicios: {servicios}\nHorario: {horario}\nZona: {zona}\n\n"
        f"INSTRUCCIONES DE AGENDA:\n"
        f"- Para gestionar citas debes usar la herramienta asignada de forma obligatoria.\n"
        f"Responde siempre en español de forma muy natural, concisa y profesional."
    )
    
    llm_payload = {"model": "gpt-4o-mini", "general_prompt": custom_prompt}
    if tool_name:
        llm_payload["tools"] = [tool_name]
        
    llm_res = retell_request("POST", "/create-retell-llm", llm_payload)
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error al inicializar el motor LLM.")
    llm_id = llm_res["llm_id"]
    
    # 3. Crear el agente de voz
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": "es-ES"
    })
    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error al inicializar el Agente de Voz.")
    agent_id = agent_res["agent_id"]
    
    # Mapear el ID de agente con el calendario correspondiente para evitar pérdidas de datos en las llamadas
    CALENDAR_MAPPING[agent_id] = calendar_id
    
    # 4. Encontrar un número libre en la cuenta
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents") or len(p["inbound_agents"]) == 0:
                free_number = p.get("phone_number")
                break
                
    if not free_number:
        # Salvaguarda: si no hay número, devolvemos un ID simulado para no romper el flujo visual de Wix
        free_number = "+34 (Sin número asignado en Retell)"
    else:
        retell_request("PATCH", f"/update-phone-number/{free_number}", {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
        })
    
    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}


@app.post("/create-retell-bot")
async def wix_webhook(request: Request):
    if not RETELL_API_KEY or not GOOGLE_JSON_STR or not RENDER_SERVER_URL:
        raise HTTPException(status_code=500, detail="Faltan credenciales de entorno en Render.")

    try:
        payload = await request.json()
        data = payload.get("data", payload)
    except Exception:
        raise HTTPException(status_code=400, detail="JSON corrupto.")
    
    asistente_nombre = data.get("asistente")
    nombre_negocio = data.get("nombre_negocio")
    sector = data.get("sector")
    servicios = data.get("servicios")
    horario = data.get("horario")
    zona = data.get("zona")
    email_usuario = data.get("email_usuario")
    
    if not all([asistente_nombre, nombre_negocio, sector, servicios, horario, zona, email_usuario]):
        raise HTTPException(status_code=422, detail="Todos los campos son obligatorios.")
        
    voice_id = VOICE_MAPPING.get(asistente_nombre, "openai-Alloy")
    
    try:
        calendar_service = get_calendar_service()
        calendar_body = {'summary': f"Agenda - {nombre_negocio}", 'timeZone': 'Europe/Madrid'}
        created_calendar = calendar_service.calendars().insert(body=calendar_body).execute()
        calendar_id = created_calendar['id']
        
        rule = {'scope': {'type': 'user', 'value': email_usuario}, 'role': 'editor'}
        calendar_service.acl().insert(calendarId=calendar_id, body=rule).execute()
        
        calendar_url = f"https://calendar.google.com/calendar/render?cid={calendar_id}"
        
        resultado = create_bot_automanaged(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_id)
        resultado["calendar_id"] = calendar_id
        resultado["calendar_url"] = calendar_url
        return resultado

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/retell-check-and-book")
async def retell_interaction(request: Request):
    try:
        payload = await request.json()
        agent_id = payload.get("agent_id")
        args = payload.get("args", {})
        
        action = args.get("accion")
        fecha_hora_str = args.get("fecha_hora") 
        nombre_paciente = args.get("nombre_paciente", "Paciente")
        
        # Recuperar de forma limpia el calendario vinculado a este agente de voz
        calendar_id = CALENDAR_MAPPING.get(agent_id)
        if not calendar_id:
            # Salvaguarda: Si el servidor se reinició, intentamos leer cualquier ID de respaldo
            calendar_id = args.get("calendar_id") or os.getenv("DEFAULT_CALENDAR_ID")
            if not calendar_id:
                return {"status": "error", "mensaje": "Agenda no mapeada."}

        calendar_service = get_calendar_service()
        
        # Formateo ultra-seguro de fechas para evitar errores 400 en Google API
        base_time = fecha_hora_str.replace("Z", "").split(".")[0]
        start_time = datetime.fromisoformat(base_time)
        end_time = start_time + timedelta(minutes=30)
        
        time_min = start_time.strftime("%Y-%m-%dT%H:%M:%S+02:00")
        time_max = end_time.strftime("%Y-%m-%dT%H:%M:%S+02:00")

        events_result = calendar_service.events().list(
            calendarId=calendar_id, timeMin=time_min, timeMax=time_max, singleEvents=True
        ).execute()
        is_occupied = len(events_result.get('items', [])) > 0

        if action == "comprobar":
            return {"status": "ocupado" if is_occupied else "libre"}

        elif action == "reservar":
            if is_occupied:
                return {"status": "error", "mensaje": "El hueco se acaba de ocupar."}
            
            event_body = {
                'summary': f"Cita: {nombre_paciente}",
                'description': 'Agendado automáticamente por el Asistente AI.',
                'start': {'dateTime': time_min, 'timeZone': 'Europe/Madrid'},
                'end': {'dateTime': time_max, 'timeZone': 'Europe/Madrid'},
            }
            calendar_service.events().insert(calendarId=calendar_id, body=event_body).execute()
            return {"status": "reservado", "mensaje": "Cita confirmada."}

    except Exception as e:
        print(f"⚠️ Error en interacción en vivo: {str(e)}")
        return {"status": "error", "mensaje": "Fallo en el sistema de agenda."}
