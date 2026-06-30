import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # Gestión nativa y precisa de zonas horarias en Python 3.9+
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests
import psycopg2  # Conector nativo de PostgreSQL
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from jose import JWTError, jwt  # Manejo seguro de tokens del Magic Link

app = FastAPI(title="Dansu Backend Completo con Magic Link")

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== VARIABLES DE ENTOR NO ====================\nRETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, BREVO_API_KEY]):
    raise Exception("Faltan variables de entorno críticas (RETELL_API_KEY, GOOGLE_CREDENTIALS, DATABASE_URL, JWT_SECRET_KEY o BREVO_API_KEY)")

VOICE_MAPPING = {
    "Varón Enérgico": "openai-Alloy",
    "Mujer Cálida": "openai-Shimmer",
    "Varón Serio": "openai-Echo",
    "Mujer Profesional": "openai-Nova"
}

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def create_google_event(calendar_email, summary, start_iso, end_iso, bypass_availability=False):
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/calendar']
        )
        service = build('calendar', 'v3', credentials=credentials)
        
        # Validación básica de disponibilidad si no se hace bypass
        if not bypass_availability:
            events_result = service.events().list(
                calendarId=calendar_email,
                timeMin=start_iso,
                timeMax=end_iso,
                singleEvents=True
            ).execute()
            if len(events_result.get('items', [])) > 0:
                raise Exception("El horario seleccionado ya está ocupado por otra cita.")

        event = {
            'summary': summary,
            'description': 'Cita agendada automáticamente por el Asistente de Voz de Dansu AI.',
            'start': {'dateTime': start_iso, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': end_iso, 'timeZone': 'Europe/Madrid'},
        }
        event_result = service.events().insert(calendarId=calendar_email, body=event).execute()
        return event_result
    except Exception as e:
        raise Exception(f"Error en Google Calendar: {str(e)}")

def create_bot_for_client(nombre_negocio, sector, servicios, horario, calendar_email, voice_id):
    # PROMPT REFORZADO Y ROBUSTO CON FILTRADO DE INTENCIÓN INICIAL Y CONTROL ESTRICTO DE FECHAS (AÑO 2026)
    prompt_base = f"""Eres el asistente telefónico virtual de inteligencia artificial para la empresa "{nombre_negocio}" (Sector: {sector}).
Tu único objetivo es atender de forma excelente, proveer información de los servicios y agendar citas en su Google Calendar.

REGLA DE SALUDO OBLIGATORIA (PRIMERA INTERACCIÓN):
Al inicio de la llamada, saluda cordialmente y pregunta de forma explícita y exacta lo siguiente: "¿Necesita información sobre nuestros servicios o desea reservar una cita?". No asumas nada hasta que el usuario responda a esta pregunta.

INFORMACIÓN DEL NEGOCIO Y SERVICIOS:
{servicios}

HORARIO DE ATENCIÓN VÁLIDO:
{horario}

REGLAS CRÍTICAS DE AGENDAMIENTO (CONTROL DE FECHA ESTRICTO):
1. El año actual es SIEMPRE 2026 de forma fija e inmutable.
2. Si el usuario dice expresiones ambiguas temporales como "el mes que viene", "la semana que viene", "el próximo lunes" o similares, tienes ESTRICTAMENTE PROHIBIDO proceder o asumir una fecha. Debes interrumpir cortés y profesionalmente solicitando que te indique el "día exacto y el mes" que desea reservar.
3. No des por válida ninguna reserva hasta que el cliente te haya confirmado verbalmente el DÍA exactO y el MES exacto.
4. Una vez que tengas el día, el mes y una hora válida dentro del horario establecido ({horario}), procede a invocar la herramienta externa para guardar la cita.

Mantén un tono profesional, conciso y eficiente. Nunca inventes servicios que no estén listados."""

    # Llamada a la API Actualizada de Retell AI (Estructura de payloads vigente)
    url = "https://api.retellai.com/create-agent"
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Adaptado a la API moderna de Retell usando LLM integrado en el agente con respuesta estructurada
    payload = {
        "agent_name": f"Bot - {nombre_negocio}",
        "voice_id": voice_id,
        "response_engine": {
            "type": "retell-llm",
            "llm_custom_instructions": prompt_base
        }
    }
    
    res = requests.post(url, json=payload, headers=headers)
    if res.status_code not in [200, 201]:
        raise Exception(f"Retell API Error ({res.status_code}): {res.text}")
        
    retell_data = res.json()
    return retell_data.get("agent_id")

@app.post("/solicitar-magic-link")
async def solicitar_magic_link(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Email requerido")
            
        token = jwt.encode({"email": email, "exp": datetime.utcnow() + timedelta(hours=2)}, JWT_SECRET_KEY, algorithm="HS256")
        link = f"https://www.dansu.ai/area-cliente?token={token}" # Ajusta a tu URL final de Wix
        
        # Enviar vía Brevo API
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {"api-key": BREVO_API_KEY, "Content-Type": "application/json"}
        payload = {
            "sender": {"name": "Dansu AI", "email": "no-reply@dansu.ai"},
            "to": [{"email": email}],
            "subject": "Tu Acceso Seguro a Dansu AI",
            "htmlContent": f"<p>Haz clic en el siguiente enlace para entrar a tu Área de Cliente. Este enlace expira en 2 horas:</p><p><a href='{link}'><b>Iniciar sesión en mi cuenta</b></a></p>"
        }
        res = requests.post(url, json=payload, headers=headers)
        if res.status_code not in [200, 201]:
            raise Exception("Error al enviar el correo a través de Brevo")
            
        return {"status": "success", "message": "Enlace enviado"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/verificar-token")
async def verificar_token(request: Request):
    try:
        data = await request.json()
        token = data.get("token")
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        return {"status": "success", "email": payload["email"]}
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

@app.post("/get-client-bots")
async def get_client_bots(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        conn = get_db_connection()
        cur = conn.cursor()
        # Modificado para permitir múltiples asistentes mapeados correctamente por email sin colisiones
        cur.execute("SELECT * FROM asistentes WHERE email_cliente = %s", (email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()
        return {"status": "success", "bots": bots}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/verify-calendar-access")
@app.post("/verify-calendar-access/")
async def verify_calendar_access(request: Request):
    try:
        data = await request.json()
        calendar_email = data.get("calendar_email")
        create_google_event(
            calendar_email,
            "🧪 Prueba de conexión - Dansu",
            "2026-07-01T10:00:00+02:00",
            "2026-07-01T10:30:00+02:00",
            bypass_availability=True
        )
        return {"status": "success", "message": "Acceso verificado correctamente"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)
        
        email_cliente = data.get("email_cliente", "").strip().lower()
        if not email_cliente:
            raise HTTPException(status_code=400, detail="Falta vincular el email del cliente propietario.")

        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        
        agent_id = create_bot_for_client(
            data.get("nombre_negocio"), data.get("sector"), data.get("servicios"),
            data.get("horario"), data.get("calendar_email"), voice_id
        )
        
        # Insertar registro soportando multi-asistente bajo el mismo email perfectamente
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO asistentes (agent_id, email_cliente, nombre_negocio, sector, servicios, horario, calendar_email, asistente_voz)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (agent_id, email_cliente, data.get("nombre_negocio"), data.get("sector"), data.get("servicios"), data.get("horario"), data.get("calendar_email"), data.get("asistente"))
        )
        conn.commit()
        cur.close()
        conn.close()
        
        return {"status": "success", "agent_id": agent_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        
        # Regenerar prompt robusto para la actualización
        prompt_base = f"""Eres el asistente telefónico virtual de IA para la empresa "{data.get('nombre_negocio')}" (Sector: {data.get('sector')}).
Tu único objetivo es atender de forma excelente, proveer información de los servicios y agendar citas en su Google Calendar.

REGLA DE SALUDO OBLIGATORIA (PRIMERA INTERACCIÓN):
Al inicio de la llamada, saluda cordialmente y pregunta de forma explícita y exacta lo siguiente: "¿Necesita información sobre nuestros servicios o desea reservar una cita?". No asumas nada hasta que el usuario responda a esta pregunta.

INFORMACIÓN DEL NEGOCIO Y SERVICIOS:
{data.get('servicios')}

HORARIO DE ATENCIÓN VÁLIDO:
{data.get('horario')}

REGLAS CRÍTICAS DE AGENDAMIENTO (CONTROL DE FECHA ESTRICTO):
1. El año actual es SIEMPRE 2026 de forma fija e inmutable.
2. Si el usuario dice expresiones ambiguas temporales como "el mes que viene", "la semana que viene", "el próximo lunes" o similares, tienes ESTRICTAMENTE PROHIBIDO proceder o asumir una fecha. Debes interrumpir cortés y profesionalmente solicitando que te indique el "día exacto y el mes" que desea reservar.
3. No des por válida ninguna reserva hasta que el cliente te haya confirmado verbalmente el DÍA exacto y el MES exacto.
4. Una vez que tengas el día, el mes y una hora válida dentro del horario establecido ({data.get('horario')}), procede a invocar la herramienta externa para guardar la cita.

Mantén un tono profesional, conciso y eficiente."""

        # Actualización usando la API moderna de Retell
        url = f"https://api.retellai.com/update-agent/{agent_id}"
        headers = {
            "Authorization": f"Bearer {RETELL_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "agent_name": f"Bot - {data.get('nombre_negocio')}",
            "voice_id": voice_id,
            "response_engine": {
                "type": "retell-llm",
                "llm_custom_instructions": prompt_base
            }
        }
        res = requests.patch(url, json=payload, headers=headers)
        if res.status_code != 200:
            raise Exception(f"Error actualizando en Retell: {res.text}")
            
        # Actualizar en base de datos local usando llave compuesta o agent_id unívoco
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """UPDATE asistentes SET nombre_negocio=%s, sector=%s, servicios=%s, horario=%s, calendar_email=%s, asistente_voz=%s
               WHERE agent_id=%s""",
            (data.get("nombre_negocio"), data.get("sector"), data.get("servicios"), data.get("horario"), data.get("calendar_email"), data.get("asistente"), agent_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        
        return {"status": "success", "message": "Bot actualizado correctamente"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        
        # Eliminar en Retell
        url = f"https://api.retellai.com/delete-agent/{agent_id}"
        headers = {"Authorization": f"Bearer {RETELL_API_KEY}"}
        requests.delete(url, headers=headers)
        
        # Eliminar de la Base de Datos
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s", (agent_id,))
        conn.commit()
        cur.close()
        conn.close()
        
        return {"status": "success", "message": "Bot eliminado"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
