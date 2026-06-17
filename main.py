from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# CARGA DE CREDENCIALES DESDE ENTORNO
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
CAL_API_KEY = os.getenv("CAL_API_KEY")  # Tu clave cal_live_...
X_CAL_CLIENT_ID = os.getenv("X_CAL_CLIENT_ID")  # El Client ID de tu OAuth PKCE

VOICE_MAPPING = {
    "Cimo": "11labs-Adrian", "Brynne": "11labs-Brynne", "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova", "Grace": "openai-Shimmer", "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa", "Lily": "11labs-Lily", "Della": "11labs-Delia",
    "Nico": "openai-Onyx", "Rita": "11labs-Rita", "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa", "Maren": "11labs-Maren", "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley", "Andrea": "openai-Alloy", "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby", "Alejandro": "openai-Echo", "Sloane": "11labs-Sloane"
}

def retell_request(method, endpoint, json_data=None):
    if not RETELL_API_KEY:
        return None
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data)
        return r.json() if r.ok else None
    except Exception:
        return None

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id):
    custom_prompt = (
        f"Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.\n"
        f"Servicios: {servicios}\nHorario: {horario}\nZona: {zona}\n"
        f"Responde en español de forma natural, concisa y profesional."
    )
    llm_res = retell_request("POST", "/create-retell-llm", {"model": "gpt-4.1-mini", "general_prompt": custom_prompt})
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")
    llm_id = llm_res["llm_id"]
    
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": "es-ES"
    })
    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agente")
    agent_id = agent_res["agent_id"]
    
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents") or len(p["inbound_agents"]) == 0:
                free_number = p.get("phone_number")
                break
                
    if not free_number:
        raise Exception("No hay números libres")
        
    retell_request("PATCH", f"/update-phone-number/{free_number}", {
        "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
    })
    
    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}

@app.post("/create-retell-bot")
async def wix_webhook(request: Request):
    if not RETELL_API_KEY or not CAL_API_KEY or not X_CAL_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Faltan credenciales de entorno en Render.")

    try:
        payload = await request.json()
        data = payload.get("data", payload)
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido.")
    
    asistente_nombre = data.get("field:asistente") or data.get("asistente")
    nombre_negocio = data.get("field:nombre_negocio") or data.get("nombre_negocio")
    sector = data.get("field:sector") or data.get("sector")
    servicios = data.get("field:servicios") or data.get("servicios")
    horario = data.get("field:horario") or data.get("horario")
    zona = data.get("field:zona") or data.get("zona")
    email_usuario = data.get("field:email_usuario") or data.get("email_usuario")
    
    if not all([asistente_nombre, nombre_negocio, sector, servicios, horario, zona, email_usuario]):
        raise HTTPException(status_code=422, detail="Faltan parámetros obligatorios.")
        
    voice_id = VOICE_MAPPING.get(asistente_nombre, "openai-Alloy")
    
    try:
        # 1. Crear bot en Retell
        resultado = create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id)
        
        # 2. Configurar cabeceras v2 unificadas para el cliente PKCE
        cal_headers = {
            "Authorization": f"Bearer {CAL_API_KEY}",
            "x-cal-client-id": X_CAL_CLIENT_ID,
            "Content-Type": "application/json"
        }
        
        # Payload para registrar el Managed User
        cal_user_payload = {
            "email": email_usuario,
            "name": nombre_negocio,
            "timeZone": "Europe/Madrid"
        }
        
        calendar_url = None
        # Petición v2 de creación de usuario gestionado
        response_user = requests.post("https://api.cal.com/v2/platform/managed-users", json=cal_user_payload, headers=cal_headers)
        
        if response_user.status_code in [200, 201]:
            user_info = response_user.json()
            cal_user_data = user_info.get("data", {})
            cal_user_id = cal_user_data.get("id")
            
            if cal_user_id:
                # 3. Solicitar el enlace de onboarding de Google Calendar v2 para este cliente
                response_link = requests.post(
                    f"https://api.cal.com/v2/platform/managed-users/{cal_user_id}/google-calendar-onboarding", 
                    headers=cal_headers
                )
                if response_link.ok:
                    calendar_url = response_link.json().get("data", {}).get("url")

        resultado["calendar_url"] = calendar_url
        return resultado

    except Exception as e:
        raise HTTPException(status_code=500, detail="Ha ocurrido un error inesperado al generar el asistente.")
