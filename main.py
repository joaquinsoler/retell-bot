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

# 🔑 API Key (mejor ponerla como variable de entorno en Render)
RETELL_API_KEY = os.getenv("RETELL_API_KEY", "key_ec7376eaa103bebc81b1de6555e5")

# ====================== MAPEO DE VOCES ======================
voice_map = {
    "Cimo": "retell-Cimo",
    "Brynne": "retell-Brynne",
    "Chloe": "retell-Chloe",
    "Kate": "retell-Kate",
    "Grace": "retell-Grace",
    "Leland": "retell-Leland",
    "Marissa": "retell-Marissa",
    "Lily": "retell-Lily",
    "Delia": "retell-Delia",
    "Nico": "retell-Nico",
    "Rita": "retell-Rita",
    "Meritt": "retell-Meritt",
    "Willa": "retell-Willa",
    "Maren": "retell-Maren",
    "Tasmin": "retell-Tasmin",
    "Ashley": "retell-Ashley",
    "Andrea": "retell-Andrea",
    "Claudia": "retell-Claudia",
    "Gaby": "retell-Gaby",
    "Alejandro": "retell-Alejandro",
}

def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}",
        "Content-Type": "application/json"
    }
    r = requests.request(method, url, headers=headers, json=json_data)
    print(f"→ {method} {endpoint} → Status: {r.status_code}")
    
    try:
        return r.json() if r.ok else r.text
    except:
        return None


@app.post("/create-retell-bot")
async def create_retell_bot(request: Request):
    print("📥 Recibida petición desde Wix")

    try:
        payload = await request.json()
        # Wix a veces envuelve los datos en "data"
        data = payload.get("data", payload)
        print("Datos recibidos:", data)
    except:
        data = {}
        print("⚠️ No se pudo leer el JSON")

    # ==================== EXTRAER CAMPOS ====================
    asistente = data.get("asistente")
    nombre_negocio = data.get("nombre_negocio")
    sector = data.get("sector")
    servicios = data.get("servicios")
    horario = data.get("horario")
    zona = data.get("zona")

    print(f"📋 Datos extraídos → Negocio: {nombre_negocio} | Asistente: {asistente}")

    if not asistente or not nombre_negocio:
        raise HTTPException(status_code=422, detail="Faltan campos obligatorios: asistente y nombre_negocio")

    voice_id = voice_map.get(asistente)
    if not voice_id:
        raise HTTPException(status_code=400, detail=f"Asistente '{asistente}' no encontrado en el mapeo")

    # ==================== PROMPT PERSONALIZADO ====================
    custom_prompt = f"""Eres un asistente virtual profesional y muy amable del negocio "{nombre_negocio}".

Sector: {sector or "No especificado"}
Servicios principales: {servicios or "No especificados"}
Horario de atención: {horario or "No especificado"}
Zona de servicio: {zona or "No especificada"}

Tu objetivo es atender llamadas de clientes de forma natural, profesional y cercana. Ayuda con información del negocio, agendar citas, resolver dudas y derivar cuando sea necesario."""

    try:
        # 1. Crear LLM
        llm = retell_request("POST", "/create-retell-llm", {
            "model": "gpt-4o-mini",           # puedes cambiar a gpt-4o si prefieres
            "general_prompt": custom_prompt
        })
        llm_id = llm.get("llm_id") if isinstance(llm, dict) else None
        if not llm_id:
            raise Exception("Error al crear el LLM")

        # 2. Crear Agent
        agent = retell_request("POST", "/create-agent", {
            "agent_name": f"{nombre_negocio} - {asistente}",
            "response_engine": {"type": "retell-llm", "llm_id": llm_id},
            "voice_id": voice_id,
            "language": "es"                  # español por defecto
        })
        agent_id = agent.get("agent_id") if isinstance(agent, dict) else None
        if not agent_id:
            raise Exception("Error al crear el Agent")

        # 3. Asignar número de teléfono libre
        numbers = retell_request("GET", "/v2/list-phone-numbers")
        free_number = None
        for p in numbers.get("items", []) if isinstance(numbers, dict) else []:
            if p.get("inbound_agents") is None or len(p.get("inbound_agents", [])) == 0:
                free_number = p.get("phone_number")
                break

        if not free_number:
            raise Exception("No hay números de teléfono libres disponibles")

        # Asignar el agente al número
        retell_request("PATCH", f"/update-phone-number/{free_number}", {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
        })

        print(f"✅ ¡Bot creado exitosamente! Agent ID: {agent_id} | Número: {free_number}")

        return {
            "success": True,
            "agent_id": agent_id,
            "phone_number": free_number,
            "agent_name": f"{nombre_negocio} - {asistente}",
            "voice": asistente,
            "message": "Bot y número asignado correctamente"
        }

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    return {"status": "ok", "message": "Servidor Retell + Wix funcionando correctamente"}
