import os, sys, subprocess, json, uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import requests

# --- 1. AUTO-INSTALACIÓN (No necesitas requirements.txt) ---
def setup():
    pkgs = ["fastapi", "uvicorn", "requests", "google-auth", "google-api-python-client"]
    for p in pkgs:
        try: __import__(p.replace("-", "_"))
        except ImportError: subprocess.check_call([sys.executable, "-m", "pip", "install", p])

setup()

# Ahora importamos las librerías instaladas
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- 2. LÓGICA DE NEGOCIO ---
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
RENDER_SERVER_URL = os.getenv("RENDER_SERVER_URL")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

def get_service():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/calendar'])
    return build('calendar', 'v3', credentials=creds)

def retell_api(method, endpoint, data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    return requests.request(method, url, headers=headers, json=data).json()

@app.post("/verify-calendar-access")
async def verify(req: Request):
    data = await req.json()
    try:
        get_service().calendars().get(calendarId=data.get("calendar_id")).execute()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/create-retell-bot")
async def create_bot(req: Request):
    data = (await req.json()).get("data", await req.json())
    cal_id = data.get("calendar_id")
    
    # Herramienta de reserva
    tool = {
        "tool_name": "gestor_citas", "tool_type": "custom",
        "url": f"{RENDER_SERVER_URL}/retell-check-and-book", "method": "POST",
        "description": "Reserva citas. Pide: Nombre, Motivo, Teléfono.",
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
    retell_api("POST", "/create-tool", tool)

    # Prompt con metadato blindado
    prompt = f"Eres asistente de {data.get('nombre_negocio')}. OBLIGATORIO pedir: Nombre, Motivo, Teléfono. --- CAL_ID: {cal_id} ---"
    llm = retell_api("POST", "/create-retell-llm", {"model": "gpt-4o-mini", "general_prompt": prompt, "tools": ["gestor_citas"]})
    agent = retell_api("POST", "/create-agent", {"agent_name": "Bot Asistente", "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]}, "voice_id": "openai-Alloy"})
    return {"status": "success", "agent_id": agent["agent_id"]}

@app.post("/retell-check-and-book")
async def handle_book(req: Request):
    payload = await req.json()
    args = payload.get("args", {})
    agent = retell_api("GET", f"/get-agent/{payload.get('agent_id')}")
    llm = retell_api("GET", f"/get-retell-llm/{agent['response_engine']['llm_id']}")
    cal_id = llm['general_prompt'].split("CAL_ID:")[1].split("---")[0].strip()
    
    start = datetime.fromisoformat(args["fecha_hora"].replace("Z", ""))
    event = {
        'summary': f"Cita: {args['nombre']}",
        'description': f"Motivo: {args['motivo']}\nTel: {args['telefono']}",
        'start': {'dateTime': start.isoformat()},
        'end': {'dateTime': (start + timedelta(minutes=30)).isoformat()}
    }
    get_service().events().insert(calendarId=cal_id, body=event).execute()
    return {"status": "reservado"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
