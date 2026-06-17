import subprocess
import sys

# Instalación de dependencias
REQUIRED_PACKAGES = ["fastapi", "uvicorn", "requests", "google-auth", "google-api-python-client"]
for package in REQUIRED_PACKAGES:
    try: __import__(package.replace("-", "_"))
    except ImportError: subprocess.check_call([sys.executable, "-m", "pip", "install", package])

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import requests, json, os

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
RENDER_SERVER_URL = os.getenv("RENDER_SERVER_URL")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

def get_calendar_service():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON) if GOOGLE_SERVICE_ACCOUNT_JSON.startswith("{") else json.load(open(GOOGLE_SERVICE_ACCOUNT_JSON))
    creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/calendar'])
    return build('calendar', 'v3', credentials=creds)

def retell_request(method, endpoint, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    return requests.request(method, url, headers=headers, json=json_data).json()

@app.post("/create-retell-bot")
async def create_bot(request: Request):
    data = (await request.json()).get("data", await request.json())
    calendar_id = data.get("calendar_id")
    
    tool_definition = {
        "tool_name": "gestor_citas",
        "tool_type": "custom",
        "url": f"{RENDER_SERVER_URL}/retell-check-and-book",
        "method": "POST",
        "description": "Herramienta para comprobar y reservar citas. PIDE OBLIGATORIAMENTE: Nombre, Motivo, Teléfono.",
        "parameters": {
            "type": "object",
            "properties": {
                "accion": {"type": "string", "enum": ["comprobar", "reservar"]},
                "fecha_hora": {"type": "string", "description": "YYYY-MM-DDTHH:MM:SS"},
                "nombre": {"type": "string"}, "motivo": {"type": "string"}, "telefono": {"type": "string"}
            },
            "required": ["accion", "fecha_hora"]
        }
    }
    retell_request("POST", "/create-tool", tool_definition)

    prompt = (f"Eres el asistente de {data['nombre_negocio']}. "
              f"REGLA: Para reservar, PIDE NOMBRE, MOTIVO y TELÉFONO. "
              f"Cuando tengas los 3 datos, usa 'reservar'.\n"
              f"--- METADATA_INTERNAL_DO_NOT_DELETE: {calendar_id} ---")
    
    llm = retell_request("POST", "/create-retell-llm", {"model": "gpt-4o-mini", "general_prompt": prompt, "tools": ["gestor_citas"]})
    agent = retell_request("POST", "/create-agent", {"agent_name": f"Bot {data['nombre_negocio']}", "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]}, "voice_id": "openai-Alloy", "language": "es-ES"})
    
    return {"status": "success", "agent_id": agent["agent_id"], "phone_number": "Pendiente de asignar"}

@app.post("/retell-check-and-book")
async def handle_interaction(request: Request):
    payload = await request.json()
    args = payload.get("args", {})
    agent_id = payload.get("agent_id")
    
    # 1. Recuperar Calendar ID
    agent = retell_request("GET", f"/get-agent/{agent_id}")
    llm = retell_request("GET", f"/get-retell-llm/{agent['response_engine']['llm_id']}")
    calendar_id = llm['general_prompt'].split("METADATA_INTERNAL_DO_NOT_DELETE:")[1].split("---")[0].strip()
    
    print(f"DEBUG: Accion={args.get('accion')}, ID={calendar_id}, Datos={args}")

    service = get_calendar_service()
    start_dt = datetime.fromisoformat(args["fecha_hora"].replace("Z", ""))
    end_dt = start_dt + timedelta(minutes=30)
    
    if args["accion"] == "comprobar":
        events = service.events().list(calendarId=calendar_id, timeMin=start_dt.isoformat()+'Z', timeMax=end_dt.isoformat()+'Z').execute()
        return {"status": "ocupado" if events.get('items') else "libre"}
    
    elif args["accion"] == "reservar":
        # 2. Validación estricta
        if not (args.get("nombre") and args.get("motivo") and args.get("telefono")):
            return {"status": "error", "mensaje": "Faltan nombre, motivo o teléfono."}
        
        # 3. Inserción
        event = {
            'summary': f"Cita: {args['nombre']}",
            'description': f"Motivo: {args['motivo']}\nTel: {args['telefono']}",
            'start': {'dateTime': start_dt.isoformat()},
            'end': {'dateTime': end_dt.isoformat()}
        }
        try:
            service.events().insert(calendarId=calendar_id, body=event).execute()
            print("DEBUG: Evento insertado en Google Calendar.")
            return {"status": "reservado"}
        except Exception as e:
            print(f"DEBUG: Error Google={str(e)}")
            return {"status": "error", "mensaje": str(e)}
