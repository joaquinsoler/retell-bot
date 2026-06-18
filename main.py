from datetime import datetime

def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    # Fecha y hora actual del servidor (dinámica)
    ahora = datetime.now()
    fecha_actual = ahora.strftime("%A, %d de %B de %Y")  # Ej: jueves, 18 de junio de 2026
    hora_actual = ahora.strftime("%H:%M")

    custom_prompt = f"""Eres el asistente virtual de {nombre_negocio}, una empresa del sector {sector}.

**Fecha y hora actual:** Hoy es {fecha_actual} y son las {hora_actual}.

Información importante de la empresa:
- Servicios que ofrecemos: {servicios}
- Horario comercial: {horario}
- Zona de servicio: {zona}

**Tu personalidad:**
- Eres **amable, cercano, agradable y profesional**.
- Hablas de forma natural, cálida y cercana, como un buen recepcionista que quiere ayudar.
- Usas un tono positivo y servicial.
- Nunca eres frío ni robótico.

**Reglas para agendar citas:**
- Cuando el cliente quiera reservar, muéstrate proactivo y agradable.
- Pregunta: día y hora aproximada, motivo de la cita y número de teléfono de contacto.
- Confirma siempre los datos con el cliente antes de agendar.
- Una vez confirmados, usa la herramienta `book_appointment`.
- Si no hay disponibilidad, propón alternativas con buena actitud.

Tu objetivo principal es ayudar al cliente de la mejor forma posible y agendar citas de manera eficiente."""

    # Crear LLM
    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4.1-mini",
        "general_prompt": custom_prompt
    })
    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")

    llm_id = llm_res["llm_id"]

    # Crear Agent con Tool
    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": "es-ES",
        "tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda una cita en el calendario de Google del negocio",
            "url": "https://retell-bot.onrender.com/book-appointment",
            "method": "POST",
            "parameters": {
                "type": "object",
                "properties": {
                    "calendar_email": {"type": "string", "description": "Email del calendario"},
                    "summary": {"type": "string", "description": "Título de la cita (ej: Corte de pelo - María López)"},
                    "start_time": {"type": "string", "description": "Fecha y hora de inicio en formato ISO (ej: 2026-07-05T10:00:00+02:00)"},
                    "end_time": {"type": "string", "description": "Fecha y hora de fin en formato ISO"},
                    "description": {"type": "string", "description": "Motivo de la cita + teléfono del cliente"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time"]
            }
        }]
    })

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent")

    agent_id = agent_res["agent_id"]

    # Asignar número
    numbers = retell_request("GET", "/v2/list-phone-numbers")
    free_number = None
    if numbers and "items" in numbers:
        for p in numbers["items"]:
            if not p.get("inbound_agents"):
                free_number = p.get("phone_number")
                break

    if free_number:
        retell_request("PATCH", f"/update-phone-number/{free_number}", {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}]
        })

    return {
        "status": "success",
        "agent_id": agent_id,
        "phone_number": free_number
    }
