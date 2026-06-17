import subprocess
import sys

# Instalador automático de dependencias en Render
REQUIRED_PACKAGES = ["fastapi", "uvicorn", "requests", "google-auth", "google-auth-oauthlib", "google-api-python-client"]
for package in REQUIRED_PACKAGES:
    try:
        if package == "google-api-python-client": import googleapiclient
        elif package == "google-auth-oauthlib": import google_auth_oauthlib
        else: __import__(package.replace("-", "_"))
    except ImportError:
        print(f"📦 Instalando dependencia: {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import requests
import json
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["=*", "http://localhost", "https://*.wixsite.com", "https://*.wix.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
RENDER_SERVER_URL = os.getenv("RENDER_SERVER_URL")

# Nuevas credenciales de OAuth generadas en tu consola de Google
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

# Mapeo persistente en memoria (Agent ID -> Refresh Token del usuario) y (Agent ID -> Calendar ID de su clínica)
USER_TOKENS = {}
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

def build_oauth_flow(redirect_uri: str):
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=['https://www.googleapis.com/auth/calendar'],
        redirect_uri=redirect_uri
    )

def get_user_calendar_service(refresh_token: str):
    # Regenerar credenciales de usuario de forma dinámica usando su Refresh Token
    from google.oauth2.credentials import Credentials
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET
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

def create_bot_automanaged(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_id, refresh_token):
    # 1. Registrar Herramienta Personalizada
    tool_definition = {
        "tool_name": f"agenda_{nombre_negocio.lower().replace(' ', '_')}",
        "tool_type": "custom",
        "url": f"{RENDER_SERVER_URL}/retell-check-and-book",
        "method": "POST",
        "description": "Comprueba disponibilidad o guarda reservas en la agenda de Google Calendar del negocio.",
        "parameters": {
            "type": "object",
            "properties": {
                "accion": {"type": "string", "description": "Acción: 'comprobar' o 'reservar'."},
                "fecha_hora": {"type": "string", "description": "Formato ISO 8601 (YYYY-MM-DDTHH:MM:SS)."},
                "nombre_paciente": {"type": "string", "description": "Nombre del cliente (obligatorio para reservar)."}
            },
            "required": ["accion", "fecha_hora"]
        }
    }
    tool_res = retell_request("POST", "/create-tool", tool_definition)
    tool_name = tool_res.get("tool_name") if tool_res else None

    # 2. Registrar Prompt y LLM
    custom_prompt = (
        f"Eres el asistente virtual de {nombre_negocio}, sector {sector}.\n"
        f"Servicios: {servicios}\nHorario: {horario}\nZona: {zona}\n\n"
        f"Gestiona citas usando obligatoriamente la herramienta asignada. Habla de forma concisa y en español."
    )
    llm_payload = {"model": "gpt-4o-mini", "general_prompt": custom_prompt}
    if tool_name: llm_payload["tools"] = [tool_name]
    
    llm_res = retell_request("POST", "/create-retell-llm", llm_payload)
    if not llm_res or "llm_id" not in llm_res: raise Exception("Error creando el LLM de Retell.")
    llm_id = llm_res["llm_id"]

    # 3. Registrar Agente
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": "es-ES"
    })
    if not agent_res or "agent_id" not in agent_res: raise Exception("Error creando el Agente de Retell.")
    agent_id = agent_res["agent_id"]

    # Mapeos internos del negocio en memoria
    USER_TOKENS[agent_id] = refresh_token
    CALENDAR_MAPPING[agent_id] = calendar_id

    # 4. Asignar número libre
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = "+34 (Asignando número...)"
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents") or len(p["inbound_agents"]) == 0:
                free_number = p.get("phone_number")
                break
    if free_number != "+34 (Asignando número...)":
        retell_request("PATCH", f"/update-phone-number/{free_number}", {"inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]})

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}


# =====================================================================
# ENDPOINT PRINCIPAL: INTERCAMBIA CÓDIGO DE GOOGLE Y DESPLIEGA EL SAAS
# =====================================================================
@app.post("/create-retell-bot")
async def wix_webhook(request: Request):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Faltan las credenciales GOOGLE_CLIENT de OAuth en Render.")

    try:
        payload = await request.json()
        data = payload.get("data", payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Payload corrupto.")

    code = data.get("code")
    redirect_uri = data.get("redirect_uri") # Enviado dinámicamente desde Wix

    if not code or not redirect_uri:
        raise HTTPException(status_code=400, detail="Falta el código de autorización OAuth o la URI de redireccionamiento.")

    try:
        # A. Intercambiar el código temporal por los tokens de acceso reales del usuario
        flow = build_oauth_flow(redirect_uri)
        flow.fetch_token(code=code)
        credentials = flow.credentials

        if not credentials.refresh_token:
            # Salvaguarda si Google no envía el refresh token por falta de parámetros de acceso
            raise Exception("No se obtuvo el Refresh Token. Desconecta la app de tu cuenta de Google e inténtalo de nuevo.")

        refresh_token = credentials.refresh_token
        
        # B. Crear el calendario secundario AUTOMÁTICAMENTE dentro de la cuenta del usuario
        user_calendar_service = get_user_calendar_service(refresh_token)
        nombre_negocio = data.get("nombre_negocio", "Mi Clínica AI")
        
        calendar_body = {'summary': f"Agenda Inteligente - {nombre_negocio}", 'timeZone': 'Europe/Madrid'}
        created_calendar = user_calendar_service.calendars().insert(body=calendar_body).execute()
        calendar_id = created_calendar['id']

        # C. Desplegar bot en Retell pasando el ID del nuevo calendario
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        bot_data = create_bot_automanaged(
            nombre_negocio, data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("zona"), voice_id, calendar_id, refresh_token
        )

        bot_data["calendar_id"] = calendar_id
        return bot_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fallo en el despliegue automático: {str(e)}")


# =====================================================================
# ENDPOINT DE INTERACCIÓN EN TIEMPO REAL DURANTE LA LLAMADA
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

        refresh_token = USER_TOKENS.get(agent_id)
        calendar_id = CALENDAR_MAPPING.get(agent_id)

        if not refresh_token or not calendar_id:
            return {"status": "error", "mensaje": "Tokens o Agenda no indexada."}

        # Conectar de forma segura a su API usando su token guardado en memoria
        user_calendar_service = get_user_calendar_service(refresh_token)

        base_time = fecha_hora_str.replace("Z", "").split(".")[0]
        start_time = datetime.fromisoformat(base_time)
        end_time = start_time + timedelta(minutes=30)

        time_min = start_time.strftime("%Y-%m-%dT%H:%M:%S+02:00")
        time_max = end_time.strftime("%Y-%m-%dT%H:%M:%S+02:00")

        events_result = user_calendar_service.events().list(
            calendarId=calendar_id, timeMin=time_min, timeMax=time_max, singleEvents=True
        ).execute()
        is_occupied = len(events_result.get('items', [])) > 0

        if action == "comprobar":
            return {"status": "ocupado" if is_occupied else "libre"}
        elif action == "reservar":
            if is_occupied: return {"status": "error", "mensaje": "Hueco ocupado."}
            event_body = {
                'summary': f"Cita AI: {nombre_paciente}",
                'start': {'dateTime': time_min, 'timeZone': 'Europe/Madrid'},
                'end': {'dateTime': time_max, 'timeZone': 'Europe/Madrid'},
            }
            user_calendar_service.events().insert(calendarId=calendar_id, body=event_body).execute()
            return {"status": "reservado", "mensaje": "Confirmada."}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}
