@app.post("/verify-calendar-access")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")

        if not calendar_email:
            raise HTTPException(status_code=400, detail="Falta el email del calendario")

        # Crear evento de prueba
        test_event = create_google_event(
            calendar_email=calendar_email,
            summary="🧪 Prueba de conexión - Dansu",
            start_time="2026-06-25T10:00:00+02:00",   # Fecha en el futuro
            end_time="2026-06-25T10:30:00+02:00",
            description="Este es un evento de prueba creado por Dansu para verificar permisos. Se eliminará automáticamente."
        )

        # Opcional: eliminar el evento de prueba inmediatamente
        # service = get_calendar_service()
        # service.events().delete(calendarId=calendar_email, eventId=test_event['id']).execute()

        return {
            "status": "success",
            "message": "Acceso verificado correctamente",
            "event_link": test_event.get("htmlLink")
        }

    except Exception as e:
        print(f"❌ Error verificando calendario {calendar_email}: {str(e)}")
        raise HTTPException(
            status_code=403, 
            detail="No se pudo acceder al calendario. Verifica que hayas dado los permisos correctos."
        )
