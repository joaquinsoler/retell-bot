from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import requests
import json
import os

app = FastAPI()

# Habilitar CORS para que tu frontend en Wix se comunique sin bloqueos
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
        print(f"⚠️ Error en petición a Retell ({endpoint}): {str(e)}")
        return None

def create_bot_automanaged(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_id):
    # 1. CREAR LA HERRAMIENTA PERSONALIZADA (CUSTOM TOOL) DINÁMICA EN RETELL
    tool_definition = {
        "tool_name": f"agenda_{nombre_negocio.lower().replace(' ', '_')}",
        "tool_type": "custom",
        "url": f"{RENDER_SERVER_URL}/retell-check-and-book",
        "method": "POST",
        "description": "Utiliza esta herramienta para comprobar la disponibilidad de citas o para confirmar una reserva en la agenda.",
        "parameters": {
            "type": "object",
            "properties": {
                "accion": {"type": "string", "description": "Determina la acción a realizar: 'comprobar' para mirar huecos o 'reservar' para agendar la cita."},
                "fecha_hora": {"type": "string", "description": "La fecha y hora que desea el cliente en formato ISO 8601 (YYYY-MM-DDTHH:MM:SS)."},
                "nombre_paciente": {"type": "string", "description": "El nombre completo del cliente que reserva (obligatorio solo para reservar)."}
            },
            "required": ["accion", "fecha_hora"]
        }
    }
    
    tool_res = retell_request("POST", "/create-tool", tool_definition)
    tool_name = tool_res.get("tool_name") if tool_res else None

    # 2. CONFIGURAR EL LLM E INYECTAR EL CALENDAR_ID EN EL PROMPT DE LA IA
    custom_prompt = (
        f"Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.\n"
        f"Servicios: {servicios}\nHorario: {horario}\nZona: {zona}\n\n"
        f"INSTRUCCIONES DE AGENDA DE CITAS:\n"
        f"- Para gestionar citas debes usar la herramienta asignada de forma obligatoria.\n"
        f"- El ID del calendario del cliente para tus consultas internas es de forma estricta: {calendar_id}\n"
        f"- Cuando uses la herramienta, pasa siempre de forma invisible este valor en el payload si es requerido.\n"
        f"Responde siempre en español de forma muy natural, concisa y profesional."
    )
    
    llm_payload = {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt
    }
    if tool_name:
        llm_payload["tools"] = [tool_name]
        
    llm_res = retell_request("POST", "/create-retell-llm", llm_payload)
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando el LLM en Retell")
    llm_id = llm_res["llm_id"]
    
    # 3. CREAR EL AGENTE DE VOZ ASOCIADO AL LLM
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": "es-ES"
    })
    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando el Agente de Voz en Retell")
    agent_id = agent_res["agent_id"]
    
    # 4. BUSCAR UN NÚMERO TELEFÓNICO LIBRE E INCOPORARLO AL AGENTE
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents") or len(p["inbound_agents"]) == 0:
                free_number = p.get("phone_number")
                break
                
    if not free_number:
        raise Exception("No hay números telefónicos libres en tu inventario de Retell.")
        
    retell_request("PATCH", f"/update-phone-number/{free_number}", {
        "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
    })
    
    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}


# =====================================================================
# ENDPOINT 1: PROCESA EL ALTA COMPLETA (WIX -> RENDER)
# =====================================================================
@app.post("/create-retell-bot")
async def wix_webhook(request: Request):
    if not RETELL_API_KEY or not GOOGLE_JSON_STR or not RENDER_SERVER_URL:
        raise HTTPException(status_code=500, detail="Faltan variables de entorno esenciales en Render.")

    try:
        payload = await request.json()
        data = payload.get("data", payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Estructura JSON inválida.")
    
    asistente_nombre = data.get("asistente")
    nombre_negocio = data.get("nombre_negocio")
    sector = data.get("sector")
    servicios = data.get("servicios")
    horario = data.get("horario")
    zona = data.get("zona")
    email_usuario = data.get("email_usuario")
    
    if not all([asistente_nombre, nombre_negocio, sector, servicios, horario, zona, email_usuario]):
        raise HTTPException(status_code=422, detail="Faltan parámetros obligatorios en el formulario.")
        
    voice_id = VOICE_MAPPING.get(asistente_nombre, "openai-Alloy")
    
    try:
        # A. Crear Google Calendar secundario e invisible para la clínica
        calendar_service = get_calendar_service()
        calendar_body = {'summary': f"Agenda - {nombre_negocio}", 'timeZone': 'Europe/Madrid'}
        created_calendar = calendar_service.calendars().insert(body=calendar_body).execute()
        calendar_id = created_calendar['id']
        
        # B. Darle permisos de Editor al email real del dentista/barbero
        rule = {'scope': {'type': 'user', 'value': email_usuario}, 'role': 'editor'}
        calendar_service.acl().insert(calendarId=calendar_id, body=rule).execute()
        
        # Enlace oficial de suscripción mágica para móviles
        calendar_url = f"https://calendar.google.com/calendar/render?cid={calendar_id}"
        
        # C. Desplegar toda la infraestructura en Retell por API
        resultado = create_bot_automanaged(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_id)
        
        resultado["calendar_id"] = calendar_id
        resultado["calendar_url"] = calendar_url
        return resultado

    except Exception as e:
        print(f"❌ Error crítico en el flujo de creación: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================================
# ENDPOINT 2: RECIBE LAS INTERACCIONES DE LA IA EN TIEMPO REAL (RETELL -> RENDER)
# =====================================================================
@app.post("/retell-check-and-book")
async def retell_interaction(request: Request):
    try:
        payload = await request.json()
        args = payload.get("args", {})
        
        action = args.get("accion")
        fecha_hora_str = args.get("fecha_hora") 
        nombre_paciente = args.get("nombre_paciente", "Paciente Anónimo")
        
        # Retell extrae el 'calendar_id' directamente desde las instrucciones de su prompt
        calendar_id = args.get("calendar_id")
        if not calendar_id:
            return {"status": "error", "mensaje": "ID de agenda no especificado por la IA."}

        calendar_service = get_calendar_service()
        
        # Parsear fechas y calcular rango (Asumimos franjas fijas de 30 minutos)
        start_time = datetime.fromisoformat(fecha_hora_str.replace("Z", ""))
        end_time = start_time + timedelta(minutes=30)
        
        # Configurar zona horaria UTC+2 (Horario de España)
        time_min = start_time.isoformat() + "+02:00" 
        time_max = end_time.isoformat() + "+02:00"

        # Comprobar si hay algún evento pisando ese hueco
        events_result = calendar_service.events().list(
            calendarId=calendar_id, timeMin=time_min, timeMax=time_max, singleEvents=True
        ).execute()
        is_occupied = len(events_result.get('items', [])) > 0

        if action == "comprobar":
            return {"status": "ocupado" if is_occupied else "libre"}

        elif action == "reservar":
            if is_occupied:
                return {"status": "error", "mensaje": "Lo siento, el hueco se ha ocupado."}
            
            event_body = {
                'summary': f"Cita: {nombre_paciente}",
                'description': 'Agendado automáticamente por el Asistente de Voz AI.',
                'start': {'dateTime': time_min, 'timeZone': 'Europe/Madrid'},
                'end': {'dateTime': time_max, 'timeZone': 'Europe/Madrid'},
            }
            calendar_service.events().insert(calendarId=calendar_id, body=event_body).execute()
            return {"status": "reservado", "mensaje": "Cita confirmada en la agenda."}

    except Exception as e:
        print(f"❌ Error en la llamada en tiempo real de la IA: {str(e)}")
        return {"status": "error", "mensaje": "Error de comunicación con la agenda."}
