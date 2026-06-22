# ... (todo el código anterior del servidor se mantiene igual hasta la función update_retell_bot_endpoint)

# ==================== ENDPOINTS ÁREA DE CLIENTE ====================

@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    """
    Actualiza TODOS los parámetros del asistente:
    - Prompt del LLM (siempre)
    - Voz del agente (si cambia)
    - Datos en PostgreSQL
    """
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        nombre_negocio = data.get("nombre_negocio")
        sector = data.get("sector")
        servicios = data.get("servicios")
        horario = data.get("horario")
        zona = data.get("zona")
        calendar_email = data.get("google_calendar_email")
        asistente = data.get("asistente")  # NUEVO: voz

        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta el agent_id")

        # 1. Obtener información actual del agente
        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if not agent_info or "response_engine" not in agent_info:
            raise HTTPException(status_code=404, detail="Agente no encontrado en Retell AI")

        llm_id = agent_info["response_engine"].get("llm_id")
        current_voice_id = agent_info.get("voice_id")

        if not llm_id:
            raise HTTPException(status_code=400, detail="El agente no tiene LLM asociado")

        # 2. Generar nuevo prompt
        nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

        # 3. Actualizar el prompt del LLM
        llm_update = retell_request("PATCH", f"/update-retell-llm/{llm_id}", {
            "general_prompt": nuevo_prompt
        })
        if not llm_update:
            raise HTTPException(status_code=500, detail="Error al actualizar el prompt en Retell AI")

        # 4. Actualizar VOZ si ha cambiado (NUEVO)
        if asistente:
            new_voice_id = VOICE_MAPPING.get(asistente)
            if new_voice_id and new_voice_id != current_voice_id:
                voice_update = retell_request("PATCH", f"/update-agent/{agent_id}", {
                    "voice_id": new_voice_id
                })
                if voice_update:
                    print(f"✅ Voz actualizada a {asistente} ({new_voice_id})")
                else:
                    print("⚠️ No se pudo actualizar la voz (puede que sea la misma)")

        # 5. Actualizar en PostgreSQL (incluyendo asistente/voz)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio = %s, 
                sector = %s, 
                servicios = %s, 
                horario = %s, 
                zona = %s, 
                google_calendar_email = %s,
                asistente = %s
            WHERE agent_id = %s;
        """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, asistente, agent_id))
        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "message": "Asistente actualizado completamente en Retell AI y base de datos"}

    except Exception as e:
        print(f"❌ Error en update-retell-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ... (el resto del código del servidor se mantiene exactamente igual)
