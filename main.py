from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import os  # Necesario para leer de forma segura las variables de entorno

app = FastAPI()

# Configuración de CORS total para evitar bloqueos del iFrame de Wix [cite: 1]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REPOSITORIO SEGURO: Cargamos las credenciales desde el entorno de Render
RETELL_API_KEY = os.getenv("RETELL_API_KEY") [cite: 1]
CAL_API_KEY = os.getenv("CAL_API_KEY")

# Diccionario mapeado con los 21 nombres del carrusel HTML [cite: 1]
VOICE_MAPPING = {
    "Cimo": "11labs-Adrian",
    "Brynne": "11labs-Brynne",
    "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova",
    "Grace": "openai-Shimmer",
    "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa",
    "Lily": "11labs-Lily",
    "Della": "11labs-Delia", [cite: 1, 2]
    "Nico": "openai-Onyx",
    "Rita": "11labs-Rita",
    "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa",
    "Maren": "11labs-Maren",
    "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley",
    "Andrea": "openai-Alloy", [cite: 2]
    "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby",
    "Alejandro": "openai-Echo",
    "Sloane": "11labs-Sloane"
}

def retell_request(method, endpoint, json_data=None):
    if not RETELL_API_KEY:
        print("❌ Error de configuración: Falta la variable RETELL_API_KEY.")
        return None

    url = f"https://api.retellai.com{endpoint}" [cite: 2]
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}", [cite: 2]
        "Content-Type": "application/json"
    }
    try:
        r = requests.request(method, url, headers=headers, json=json_data) [cite: 2]
        print(f"→ {method} {endpoint} → {r.status_code}") [cite: 2]
        return r.json() if r.ok else None [cite: 2]
    except Exception as e:
        print(f"❌ Error en la llamada a Retell: {str(e)}") [cite: 2]
        return None

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, model="gpt-4.1-mini"):
    print(f"🤖 Creando bot para: {nombre_negocio} | Sector: {sector} | Voz: {voice_id}") [cite: 3, 4]
    
    custom_prompt = (
        f"Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.\n" [cite: 4]
        f"Tu objetivo es atender a los clientes de manera profesional y amable.\n\n" [cite: 4]
        f"Información clave de la empresa:\n" [cite: 4]
        f"- Servicios que ofrecemos: {servicios}\n" [cite: 4]
        f"- Horario de atención: {horario}\n" [cite: 4]
        f"- Zona de servicio/cobertura: {zona}\n\n" [cite: 4]
        f"Por favor, responde a las dudas de los usuarios basándote estrictamente en esta información. " [cite: 5]
        f"Habla siempre en español de forma natural y concisa." [cite: 5]
    )
    
    # 1. Crear el LLM en Retell
    llm_res = retell_request("POST", "/create-retell-llm", { [cite: 5]
        "model": model,
        "general_prompt": custom_prompt
    })
    if not llm_res or "llm_id" not in llm_res: [cite: 5]
        raise Exception("Error creando LLM en Retell") [cite: 5, 6]
    llm_id = llm_res["llm_id"] [cite: 6]
    
    # 2. Crear el Agente vinculándole el LLM y la Voz
    agent_res = retell_request("POST", "/create-agent", { [cite: 6]
        "agent_name": f"Bot {nombre_negocio}", [cite: 6]
        "response_engine": {"type": "retell-llm", "llm_id": llm_id}, [cite: 6]
        "voice_id": voice_id, [cite: 6]
        "language": "es-ES" [cite: 6]
    })
    if not agent_res or "agent_id" not in agent_res: [cite: 6]
        raise Exception("Error creando Agente en Retell") [cite: 6, 7]
    agent_id = agent_res["agent_id"] [cite: 7]
    
    # 3. Buscar número de teléfono libre
    numbers = retell_request("GET", "/v2/list-phone-numbers") [cite: 7]
    free_number = None
    if numbers and "items" in numbers: [cite: 7]
        for p in numbers["items"]: [cite: 7]
            if not p.get("inbound_agents") or len(p["inbound_agents"]) == 0: [cite: 7]
                free_number = p.get("phone_number") [cite: 7]
                break [cite: 7, 8]
                
    if not free_number:
        raise Exception("No se encontraron números de teléfono libres en Retell.") [cite: 8]
        
    # 4. Asignar agente al número libre obtenido
    retell_request("PATCH", f"/update-phone-number/{free_number}", { [cite: 8]
        "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}] [cite: 8]
    })
    
    return {"status": "success", "agent_id": agent_id, "phone_number": free_number} [cite: 9]


@app.post("/create-retell-bot")
async def wix_webhook(request: Request):
    print("📩 Recibida petición en /create-retell-bot") [cite: 9]
    
    if not RETELL_API_KEY or not CAL_API_KEY:
        raise HTTPException(status_code=500, detail="Error de configuración interna del servidor.")

    try:
        payload = await request.json() [cite: 9]
        data = payload.get("data", payload) [cite: 9]
    except Exception:
        raise HTTPException(status_code=400, detail="No se pudo procesar el JSON.") [cite: 9]
    
    asistente_nombre = data.get("field:asistente") or data.get("asistente") [cite: 9]
    nombre_negocio = data.get("field:nombre_negocio") or data.get("nombre_negocio") [cite: 10]
    sector = data.get("field:sector") or data.get("sector") [cite: 10]
    servicios = data.get("field:servicios") or data.get("servicios") [cite: 10]
    horario = data.get("field:horario") or data.get("horario") [cite: 10]
    zona = data.get("field:zona") or data.get("zona") [cite: 10]
    
    # NUEVO CAMPO: Capturamos el email que envía Wix para registrarlo en Cal.com
    email_usuario = data.get("field:email_usuario") or data.get("email_usuario")
    
    print(f"Campos procesados -> Asistente: {asistente_nombre} | Negocio: {nombre_negocio} | Email: {email_usuario}") [cite: 10, 11]
    
    if not all([asistente_nombre, nombre_negocio, sector, servicios, horario, zona, email_usuario]):
        raise HTTPException(status_code=422, detail="Faltan parámetros obligatorios.") [cite: 11]
        
    voice_id = VOICE_MAPPING.get(asistente_nombre, "openai-Alloy") [cite: 11]
    
    try:
        # 1. Crear el bot en Retell [cite: 11]
        resultado = create_bot_for_client( [cite: 11]
            nombre_negocio=nombre_negocio, 
            sector=sector, [cite: 11]
            servicios=servicios, [cite: 11]
            horario=horario, [cite: 12]
            zona=zona, [cite: 12]
            voice_id=voice_id [cite: 12]
        )
        
        # 2. Crear el sub-usuario de la clínica en tu Cal.com Teams
        calendar_url = None
        cal_headers = {
            "Authorization": f"Bearer {CAL_API_KEY}",
            "Content-Type": "application/json"
        }
        
        username_slug = nombre_negocio.lower().replace(" ", "-").replace("ñ", "n")
        cal_user_payload = {
            "email": email_usuario,
            "username": username_slug,
            "name": nombre_negocio,
            "role": "MEMBER"
        }
        
        response_user = requests.post("https://api.cal.com/v1/users", json=cal_user_payload, headers=cal_headers)
        
        if response_user.status_code in [200, 201]:
            user_info = response_user.json()
            cal_user_id = user_info["user"]["id"]
            
            # 3. Solicitar el enlace de onboarding para vincular su Google Calendar
            link_payload = { "userId": cal_user_id }
            response_link = requests.post("https://api.cal.com/v1/destination-calendars/link", json=link_payload, headers=cal_headers)
            
            if response_link.ok:
                calendar_url = response_link.json().get("url")

        # Añadimos la URL del calendario a la respuesta original
        resultado["calendar_url"] = calendar_url
        return resultado

    except Exception as e:
        print(f"❌ Error interno en Render: {str(e)}") [cite: 12]
        raise HTTPException(
            status_code=500, 
            detail="Ha ocurrido un error inesperado al generar el asistente. Por favor, inténtelo de nuevo en unos minutos."
        )
