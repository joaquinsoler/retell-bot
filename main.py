from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RETELL_API_KEY = "key_ec7376eaa103bebc81b1de6555e5"

def create_bot_for_client(client_name, custom_prompt, voice_id, language, model="gpt-4.1-mini"):
    print(f"🚀 Creando bot para: {client_name} | Voz: {voice_id} | Idioma: {language}")

    def retell_request(method, endpoint, json_data=None):
        url = f"https://api.retellai.com{endpoint}"
        headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
        r = requests.request(method, url, headers=headers, json=json_data)
        print(f"→ {method} {endpoint} → {r.status_code}")
        try:
            return r.json() if r.ok else None
        except:
            return None

    # Crear LLM
    llm = retell_request("POST", "/create-retell-llm", {
        "model": model,
        "general_prompt": custom_prompt or f"Eres el contestador automático de {client_name}. Sé profesional y amable.",
        "language": language
    })
    llm_id = llm.get("llm_id") if llm else None
    if not llm_id:
        raise Exception("Error creando LLM")

    # Crear Agent
    agent = retell_request("POST", "/create-agent", {
        "agent_name": f"{client_name} Bot",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": language
    })
    agent_id = agent.get("agent_id") if agent else None
    if not agent_id:
        raise Exception("Error creando Agent")

    # Buscar número libre
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    for p in numbers.get("items", []) if numbers else []:
        if not p.get("inbound_agents"):
            free_number = p.get("phone_number")
            break

    if not free_number:
        raise Exception("No hay números libres")

    # Asignar
    retell_request("PATCH", f"/update-phone-number/{free_number}", {
        "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
    })

    return {"success": True, "phone_number": free_number, "agent_id": agent_id}

@app.get("/create-bot")
@app.post("/create-bot")
async def create_bot(
    client_name: str = Query(None),
    voice_id: str = Query(None),
    language: str = Query(None),
    custom_prompt: str = Query(None),
    model: str = Query("gpt-4.1-mini")
):
    if not client_name or not voice_id or not language:
        raise HTTPException(status_code=422, detail="Faltan parámetros obligatorios")
    
    try:
        return create_bot_for_client(client_name, custom_prompt, voice_id, language, model)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

print("Servidor listo")
