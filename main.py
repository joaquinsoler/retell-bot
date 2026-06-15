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

# ==================== CONFIGURACIÓN ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY", "key_ec7376eaa103bebc81b1de6555e5")

# Mapeo de voces
voice_map = {
    "Cimo": "retell-Cimo", "Brynne": "retell-Brynne", "Chloe": "retell-Chloe",
    "Kate": "retell-Kate", "Grace": "retell-Grace", "Leland": "retell-Leland",
    "Marissa": "retell-Marissa", "Lily": "retell-Lily", "Delia": "retell-Delia",
    "Nico": "retell-Nico", "Rita": "retell-Rita", "Meritt": "retell-Meritt",
    "Willa": "retell-Willa", "Maren": "retell-Maren", "Tasmin": "retell-Tasmin",
    "Ashley": "retell-Ashley", "Andrea": "retell-Andrea", "Claudia": "retell-Claudia",
    "Gaby": "retell-Gaby", "Alejandro": "retell-Alejandro",
}

def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    r = requests.request(method, url, headers=headers, json=json_data)
    print(f"→ {method} {endpoint} → Status: {r.status_code}")
    try:
        return r.json() if r.ok else r.text
    except:
        return None


@app.post("/create-retell-bot")
async def create_retell_bot(request: Request):
    print("📥 POST /create-retell-bot recibido desde Wix")

    try:
        payload = await request.json()
        data = payload.get("data", payload)
        print("🔍 Payload recibido correctamente")
    except:
        raise HTTPException(status_code=400, detail="No se pudo leer el JSON")

    # ==================== EXTRAER DATOS DE WIX FORMS ====================
    asistente = None
    nombre_negocio = None
    sector = None
    servicios = None
    horario = None
    zona = None

    # Método 1: Buscar en "submissions" (lista de label/value)
    for item in data.get("submissions", []):
        label = item.get("label", "")
        value = item.get("value")
        if value:
            if "Asistente" in label:
                asistente = value
            elif "Nombre del Negocio" in label:
                nombre_negocio = value
            elif "Sector" in label or "Tipo de Negocio" in label:
                sector = value
            elif "Servicios" in label:
                servicios = value
            elif "Horario" in label:
                horario = value
            elif "Zona" in label:
                zona = value

    # Método 2: Buscar en los campos directos "field:xxx"
    asistente      = asistente      or data.get("field:asistente")
    nombre_negocio = nombre_negocio or data.get("field:nombre_negocio")
    sector         = sector         or data.get("field:sector")
    servicios      = servicios      or data.get("field:servicios")
    horario        = horario        or data.get("field:horario")
    zona           = zona           or data.get("field:zona")

    print(f"📋 Extraído → Asistente: {asistente} | Negocio: {nombre_negocio}")

    if not asistente or not nombre_negocio:
        raise HTTPException(status_code=422, detail={
            "error": "No se pudieron extraer los campos",
            "asistente": asistente,
            "nombre_negocio": nombre_negocio
        })

    voice_id = voice_map.get(asistente)
    if not voice_id:
        raise HTTPException(status_code=400, detail=f"Asistente '{asistente}' no encontrado")

    # ==================== PROMPT ====================
    custom_prompt = f"""Eres un asistente virtual profesional y amable del negocio "{nombre_negocio}".

Sector: {sector or "No especificado"}
Servicios: {servicios or "No especificados"}
Horario: {horario or "No especificado"}
Zona de servicio: {zona or "No especificada"}

Sé cercano, claro y profesional.""" 

    try:
        # 1. Crear LLM
        llm = retell_request("POST", "/create-retell-llm", {
            "model": "gpt-4o-mini",
            "general_prompt": custom_prompt
        })
        llm_id = llm.get("llm_id") if isinstance(llm, dict) else None
        if not llm_id:
            raise Exception("Error creando LLM")

        # 2. Crear Agent
        agent = retell_request("POST", "/create-agent", {
            "agent_name": f"{nombre_negocio} - {asistente}",
            "response_engine": {"type": "retell-llm", "llm_id": llm_id},
            "voice_id": voice_id,
            "language": "es"
        })
        agent_id = agent.get("agent_id") if isinstance(agent, dict) else None
        if not agent_id:
            raise Exception("Error creando Agent")

        # 3. Asignar número libre
        numbers = retell_request("GET", "/v2/list-phone-numbers")
        free_number = None
        for p in numbers.get("items", []) if isinstance(numbers, dict) else []:
            if not p.get("inbound_agents"):
                free_number = p.get("phone_number")
                break

        if not free_number:
            raise Exception("No hay números libres")

        retell_request("PATCH", f"/update-phone-number/{free_number}", {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
        })

        print(f"✅ BOT CREADO → Agent: {agent_id} | Número: {free_number}")

        return {
            "success": True,
            "agent_id": agent_id,
            "phone_number": free_number,
            "agent_name": f"{nombre_negocio} - {asistente}",
            "voice": asistente
        }

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    return {"status": "ok", "message": "Servidor funcionando correctamente"}
