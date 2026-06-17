import subprocess, sys, os, time, json, requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- CONFIGURACIÓN ---
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
RENDER_SERVER_URL = os.getenv("RENDER_SERVER_URL")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

def get_calendar_service():
    # Carga segura del JSON de credenciales
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON) if GOOGLE_SERVICE_ACCOUNT_JSON.startswith("{") else json.load(open(GOOGLE_SERVICE_ACCOUNT_JSON))
    creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/calendar'])
    return build('calendar', 'v3', credentials=creds)

def retell_request(method, endpoint, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    response = requests.request(method, url, headers=headers, json=json_data)
    if not response.ok: print(f"ERROR RETELL {endpoint}: {response.text}")
    return response.json() if response.ok else None

@app.post("/create-retell-bot")
async def create_bot(request: Request):
    data = (await request.json()).get("data", await request.json())
    calendar_id = data.get("calendar_id")
    
    # Tool Definition estricta
    tool_definition = {
        "tool_name": "gestor_citas",
        "tool_type": "custom",
        "url": f"{RENDER_SERVER_URL}/retell-check-and-book",
        "method": "POST",
        "description": "Si quieres reservar una cita, PIDE NOMBRE, MOTIVO Y TELÉFONO. Luego usa esta herramienta.",
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

    # Prompt con Metadata oculta para persistencia
    prompt = (f"Eres el asistente de {data.get('nombre_negocio', 'tu empresa')}. "
              f"Tu prioridad es agendar citas. OBLIGATORIO: Pide Nombre, Motivo y Teléfono antes de reservar. "
              f"--- METADATA: {calendar_id} ---")
    
    llm = retell_request("POST", "/create-retell-llm", {"model": "gpt-4o-mini", "general_prompt": prompt, "tools": ["gestor_citas"]})
    agent = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {data.get('nombre_negocio')}",
        "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]},
        "voice_id": "openai-Alloy"
    })
    
    return {"status": "success", "agent_id": agent["agent_id"]}

@app.post("/retell-check-and-book")
async def handle_interaction(request: Request):
    payload = await request.json()
    args = payload.get("args", {})
    agent_id = payload.get("agent_id")
    
    # 1. Recuperar ID del calendario desde el prompt del LLM
    agent = retell_request("GET", f"/get-agent/{agent_id}")
    llm = retell_request("GET", f"/get-retell-llm/{agent['response_engine']['llm_id']}")
    calendar_id = llm['general_prompt'].split("METADATA:")[1].split("---")[0].strip()
    
    service = get_calendar_service()
    start_dt = datetime.fromisoformat(args["fecha_hora"].replace("Z", ""))
    
    if args["accion"] == "reservar":
        # 2. Inserción con validación de datos recibidos
        event = {
            'summary': f"Cita: {args.get('nombre', 'Cliente')}",
            'description': f"Motivo: {args.get('motivo', 'N/A')}\nTel: {args.get('telefono', 'N/A')}",
            'start': {'dateTime': start_dt.isoformat()},
            'end': {'dateTime': (start_dt + timedelta(minutes=30)).isoformat()}
        }
        try:
            service.events().insert(calendarId=calendar_id, body=event).execute()
            return {"status": "reservado"}
        except Exception as e:
            return {"status": "error", "mensaje": str(e)}
            
    return {"status": "comprobado"}

if __name__ == "__main__":
    # Render asigna el puerto mediante PORT
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
