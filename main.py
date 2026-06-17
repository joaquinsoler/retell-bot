import subprocess
import sys
import os

# --- RUTINA DE AUTO-INSTALACIÓN ---
def install_requirements():
    required = ["fastapi", "uvicorn", "requests", "google-auth", "google-api-python-client"]
    for package in required:
        try:
            __import__(package.replace("-", "_"))
        except ImportError:
            print(f"Instalando dependencia: {package}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])

install_requirements()

# --- CÓDIGO PRINCIPAL ---
import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import requests

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

RETELL_API_KEY = os.getenv("RETELL_API_KEY")
RENDER_SERVER_URL = os.getenv("RENDER_SERVER_URL")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

def get_calendar_service():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/calendar'])
    return build('calendar', 'v3', credentials=creds)

def retell_request(method, endpoint, data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    r = requests.request(method, url, headers=headers, json=data)
    return r.json() if r.ok else None

@app.post("/create-retell-bot")
async def create_bot(request: Request):
    data = (await request.json()).get("data", await request.json())
    calendar_id = data.get("calendar_id")
    
    # Herramienta con validación estricta
    tool = {
        "tool_name": "gestor_citas",
        "tool_type": "custom",
        "url": f"{RENDER_SERVER_URL}/retell-check-and-book",
        "method": "POST",
        "description": "Reserva citas. PIDE SIEMPRE: Nombre, Motivo, Teléfono.",
        "parameters": {
            "type": "object",
            "properties": {
                "accion": {"type": "string", "enum": ["reservar"]},
                "fecha_hora": {"type": "string"}, "nombre": {"type": "string"},
                "motivo": {"type": "string"}, "telefono": {"type": "string"}
            },
            "required": ["accion", "fecha_hora", "nombre", "motivo", "telefono"]
        }
    }
    retell_request("POST", "/create-tool", tool)

    # Prompt con Metadata persistente
    prompt = f"Eres el asistente. OBLIGATORIO pedir: Nombre, Motivo, Teléfono antes de reservar. --- CAL_ID: {calendar_id} ---"
    llm = retell_request("POST", "/create-retell-llm", {"model": "gpt-4o-mini", "general_prompt": prompt, "tools": ["gestor_citas"]})
    
    agent = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {data.get('nombre_negocio', 'Asistente')}",
        "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]},
        "voice_id": "openai-Alloy"
    })
    return {"status": "success", "agent_id": agent["agent_id"]}

@app.post("/retell-check-and-book")
async def handle_interaction(request: Request):
    payload = await request.json()
    args = payload.get("args", {})
    agent_id = payload.get("agent_id")
    
    # Recuperar cal_id del prompt
    agent = retell_request("GET", f"/get-agent/{agent_id}")
    llm = retell_request("GET", f"/get-retell-llm/{agent['response_engine']['llm_id']}")
    cal_id = llm['general_prompt'].split("CAL_ID:")[1].split("---")[0].strip()
    
    start = datetime.fromisoformat(args["fecha_hora"].replace("Z", ""))
    
    # Inserción directa
    event = {
        'summary': f"Cita: {args['nombre']}",
        'description': f"Motivo: {args['motivo']}\nTel: {args['telefono']}",
        'start': {'dateTime': start.isoformat()},
        'end': {'dateTime': (start + timedelta(minutes=30)).isoformat()}
    }
    get_calendar_service().events().insert(calendarId=cal_id, body=event).execute()
    return {"status": "reservado"}

if __name__ == "__main__":
    # Render asigna el puerto mediante PORT
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
