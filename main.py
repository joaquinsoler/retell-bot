from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI()

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RETELL_API_KEY = "key_ec7376eaa103bebc81b1de6555e5"

# Diccionario mapeado con los 21 nombres del carrusel HTML
VOICE_MAPPING = {
    "Cimo": "11labs-Adrian",
    "Brynne": "11labs-Brynne",
    "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova",
    "Grace": "openai-Shimmer",
    "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa",
    "Lily": "11labs-Lily",
    "Della": "11labs-Delia",
    "Nico": "openai-Onyx",
    "Rita": "11labs-Rita",
    "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa",
    "Maren": "11labs-Maren",
    "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley",
    "Andrea": "openai-Alloy", 
    "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby",
    "Alejandro": "openai-Echo",
    "Sloane": "11labs-Sloane"
}

def retell_request(method, endpoint, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}",
        "Content-Type": "application/json"
    }
    r = requests.request(method, url, headers=headers, json=json_data)
    print(f"→ {method} {endpoint} → {r.status_code}")
    try:
        return r.json() if r.ok else None
    except:
        return None

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, model="gpt-4.1-mini"):
    print(f"🤖 Creando bot para: {nombre_negocio} | Sector: {sector} | Voz: {voice_id}")
    
    custom_prompt = (
        f"Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.\n"
        f"Tu objetivo es atender a los clientes de manera profesional y amable.\n\n"
        f"Información clave de la empresa:\n"
        f"- Servicios que ofrecemos: {servicios}\n"
        f"- Horario de atención: {horario}\n"
        f"- Zona de servicio/cobertura: {zona}\n\n"
        f"Por favor, responde a las dudas de los usuarios basándote estrictamente en esta información. "
        f"Habla siempre en español de forma natural y concisa."
    )
    
    # 1. Crear el LLM en Retell
    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": model,
        "general_prompt": custom_prompt
    })
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM en Retell")
    llm_id = llm_res["llm_id"]
    
    # 2. Crear el Agente vinculándole el LLM y la Voz
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": "es-ES"
    })
    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agente en Retell")
    agent_id = agent_res["agent_id"]
    
    # 3. Buscar número de teléfono libre
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents") or len(p["inbound_agents"]) == 0:
                free_number = p.get("phone_number")
                break
                
    if not free_number:
        raise Exception("No se encontraron números de teléfono libres en Retell.")
        
    # 4. Asignar agente al número libre obtenido
    retell_request("PATCH", f"/update-phone-number/{free_number}", {
        "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
    })
    
    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}


@app.post("/create-retell-bot")
async def wix_webhook(request: Request):
    print("📩 Recibida petición desde el HTML de Wix en /create-retell-bot")
    try:
        payload = await request.json()
        data = payload.get("data", payload)
    except Exception:
        raise HTTPException(status_code=400, detail="No se pudo procesar el JSON.")
    
    # Extracción de campos con el prefijo "field:" enviado por tu iFrame HTML
    asistente_nombre = data.get("field:asistente") or data.get("asistente")
    nombre_negocio = data.get("field:nombre_negocio") or data.get("nombre_negocio")
    sector = data.get("field:sector") or data.get("sector")
    servicios = data.get("field:servicios") or data.get("servicios")
    horario = data.get("field:horario") or data.get("horario")
    zona = data.get("field:zona") or data.get("zona")
    
    print(f"Campos procesados -> Asistente: {asistente_nombre} | Negocio: {nombre_negocio}")
    
    if not all([asistente_nombre, nombre_negocio, sector, servicios, horario, zona]):
        raise HTTPException(status_code=422, detail="Faltan parámetros obligatorios en el JSON recibido.")
        
    voice_id = VOICE_MAPPING.get(asistente_nombre, "openai-Alloy")
    
    try:
        resultado = create_bot_for_client(
            nombre_negocio=nombre_negocio, 
            sector=sector, 
            servicios=servicios, 
            horario=horario, 
            zona=zona, 
            voice_id=voice_id
        )
        return resultado
    except Exception as e:
        print(f"❌ Error en el proceso interno: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

print("🚀 Servidor listo para peticiones del iFrame HTML de Wix")
