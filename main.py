import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Dansu Backend - Completo y Mejorado")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")

if not RETELL_API_KEY or not GOOGLE_CREDENTIALS_JSON or not DATABASE_URL:
    raise Exception("Faltan variables de entorno críticas")

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== BASE DE DATOS ====================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS asistentes (
            id SERIAL PRIMARY KEY,
            nombre_negocio VARCHAR(255),
            sector VARCHAR(255),
            servicios TEXT,
            horario VARCHAR(255),
            zona VARCHAR(255),
            google_calendar_email VARCHAR(255),
            asistente VARCHAR(255),
            agent_id VARCHAR(255) UNIQUE,
            phone_number VARCHAR(255),
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Base de datos PostgreSQL inicializada.")

init_db()

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar']
MADRID_TZ = ZoneInfo("Europe/Madrid")

def get_calendar_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=SCOPES
    )
    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

def ensure_calendar_access(calendar_id: str):
    try:
        service = get_calendar_service()
        service.calendarList().insert(body={'id': calendar_id}).execute()
    except HttpError as e:
        if e.status_code != 409:
            print(f"⚠️ Error suscripción calendario: {e}")

def normalize_to_madrid_iso(dt_str: str) -> str:
    if not dt_str:
        return dt_str
    dt_str = str(dt_str).strip().replace(" ", "T")
    if dt_str.endswith("Z"):
        dt = datetime.fromisoformat(dt_str[:-1]).replace(tzinfo=ZoneInfo("UTC"))
    else:
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MADRID_TZ)
        except ValueError:
            return dt_str
    return dt.astimezone(MADRID_TZ).isoformat()

def check_availability(calendar_id: str, start_time: str, end_time: str) -> bool:
    try:
        service = get_calendar_service()
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)
        
        body = {
            "timeMin": iso_start,
            "timeMax": iso_end,
            "timeZone": "Europe/Madrid",
            "items": [{"id": calendar_id}]
        }
        freebusy = service.freebusy().query(body=body).execute()
        busy = freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy) == 0
    except Exception as e:
        print(f"⚠️ Error FreeBusy: {e}. Permitiendo agendamiento por seguridad.")
        return True

def create_google_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", bypass_availability: bool = False):
    try:
        ensure_calendar_access(calendar_id)
        iso_start = normalize_to_madrid_iso(start_time)
        iso_end = normalize_to_madrid_iso(end_time)

        if not bypass_availability and not check_availability(calendar_id, iso_start, iso_end):
            raise Exception("Horario no disponible")

        service = get_calendar_service()
        event = {
            'summary': summary[:100],
            'description': description or "Cita agendada por Dansu AI",
            'start': {'dateTime': iso_start, 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': iso_end, 'timeZone': 'Europe/Madrid'},
            'reminders': {'useDefault': True}
        }
        created = service.events().insert(calendarId=calendar_id, body=event, sendUpdates='none').execute()
        print(f"✅ Evento creado: {created.get('htmlLink')}")
        return created
    except Exception as e:
        print(f"❌ Error Google Calendar: {e}")
        raise

# ==================== RETELL & VOICE ====================
VOICE_MAPPING = {
    "Cimo": "11labs-Adrian", "Brynne": "11labs-Brynne", "Chloe": "11labs-Chloe",
    "Kate": "openai-Nova", "Grace": "openai-Shimmer", "Leland": "11labs-Leland",
    "Marissa": "11labs-Marissa", "Lily": "11labs-Lily", "Della": "11labs-Delia",
    "Nico": "openai-Onyx", "Rita": "11labs-Rita", "Meritt": "11labs-Meritt",
    "Willa": "11labs-Willa", "Maren": "11labs-Maren", "Tasmin": "11labs-Tasmin",
    "Ashley": "11labs-Ashley", "Andrea": "openai-Alloy", "Claudia": "11labs-Claudia",
    "Gaby": "11labs-Gaby", "Alejandro": "openai-Echo", "Sloane": "11labs-Sloane"
}

def retell_request(method: str, endpoint: str, json_data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        print(f"→ Retell {method} {endpoint} → {r.status_code}")
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ Error Retell: {e}")
        return None

# ==================== PROMPT MEJORADO Y ROBUSTO ====================
def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email):
    return f"""Eres el asistente virtual oficial y exclusivo de **{nombre_negocio}**, un negocio especializado en el sector de **{sector}**.

Tu única misión es ofrecer una experiencia de atención al cliente excelente: dar información clara y precisa del negocio y agendar citas de forma profesional y eficiente.

### REGLAS ABSOLUTAS (NUNCA las rompas)
- SOLO puedes: dar información del negocio y agendar **nuevas citas**.
- NUNCA canceles, modifiques ni consultes citas existentes. Si el cliente pide cancelar o modificar, responde educadamente:
  "Entiendo perfectamente. Actualmente solo puedo facilitarte información y agendar nuevas citas. Para cancelar o modificar una reserva existente, te recomiendo contactar directamente con nuestro equipo a través de WhatsApp o teléfono habitual."
- NUNCA inventes información, horarios, precios ni servicios.
- NUNCA menciones palabras técnicas (prompt, herramienta, API, error, backend, JSON, etc.). Mantente siempre en rol humano y comercial.
- Habla siempre en español natural, cálido, profesional y cercano. Usa frases cortas y claras.

### PERSONALIDAD Y ESTILO
- Tono: amable, empático, seguro y con trato comercial impecable.
- Sé proactivo pero nunca agresivo.
- Escucha activamente y adapta el ritmo al cliente.

### INFORMACIÓN DEL NEGOCIO (usa solo estos datos)
- Nombre: {nombre_negocio}
- Sector: {sector}
- Servicios: {servicios}
- Horario: {horario}
- Zona: {zona}
- Email calendario: {calendar_email}

### FLUJO NATURAL PARA AGENDAR
1. Entiende la necesidad y da información si la pide.
2. Cuando quiera agendar:
   - Pregunta de forma conversacional: día/hora preferida, nombre completo, teléfono y servicio.
3. **Antes de llamar a la herramienta**, resume todos los datos y pide confirmación explícita:
   Ejemplo: "Entonces te agendaría el [día] a las [hora] para [servicio] a nombre de [nombre]. ¿Es correcto?"
4. Solo cuando confirme ("sí", "correcto", "adelante"), llama a `book_appointment`.

### USO DE LA HERRAMIENTA `book_appointment`
- Llama **solo una vez** cuando tengas todos los datos confirmados.
- Pasa obligatoriamente `calendar_email`: `{calendar_email}`
- Campos requeridos: `summary`, `start_time`, `end_time`.

### MANEJO DE ERRORES Y SITUACIONES DIFÍCILES
- Si el horario está ocupado: "Disculpa, ese horario ya no está disponible. ¿Te vienen bien estas alternativas...?"
- Si el cliente está enfadado: "Lamento mucho las molestias. Cuéntame qué ha pasado para ayudarte."
- Si la conversación es confusa: resume y pregunta cómo proceder.
- Si no puedes resolver algo: deriva educadamente al equipo humano.

### CIERRE DE LLAMADA
Una vez agendada con éxito:
- Confirma los detalles finales.
- Agradece y ofrece ayuda adicional.
- Ejemplo: "¡Perfecto! Tu cita ha quedado confirmada para el [fecha y hora]. Te enviaremos un recordatorio. ¿Hay algo más en lo que pueda ayudarte?"

Recuerda: el cliente debe colgar sintiéndose bien atendido y con su cita confirmada (o con una buena alternativa).

Ahora responde de forma natural manteniendo siempre estas reglas."""

# ==================== CREACIÓN DE BOT ====================
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email):
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

    llm_res = retell_request("POST", "/create-retell-llm", {
        "model": "gpt-4o-mini",
        "general_prompt": custom_prompt,
        "general_tools": [{
            "type": "custom",
            "name": "book_appointment",
            "description": "Agenda la cita en el calendario del negocio.",
            "url": "https://retell-bot.onrender.com/book-appointment",
            "method": "POST",
            "parameters": {
                "type": "object",
                "properties": {
                    "calendar_email": {"type": "string"},
                    "summary": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "description": {"type": "string"}
                },
                "required": ["calendar_email", "summary", "start_time", "end_time"]
            }
        }]
    })

    if not llm_res or "llm_id" not in llm_res:
        raise Exception("Error creando LLM")

    agent_res = retell_request("POST", "/create-agent", {
        "agent_name": f"Bot {nombre_negocio}",
        "response_engine": {"type": "retell-llm", "llm_id": llm_res["llm_id"]},
        "voice_id": voice_id,
        "language": "es-ES"
    })

    if not agent_res or "agent_id" not in agent_res:
        raise Exception("Error creando Agent")

    agent_id = agent_res["agent_id"]

    # Asignar número libre
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

    # Guardar en DB
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, google_calendar_email, asistente, agent_id, phone_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
    """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, voice_id, agent_id, free_number))
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "success", "agent_id": agent_id, "phone_number": free_number}

# ==================== ENDPOINTS ====================

@app.post("/get-user-bots")
async def get_user_bots(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()
        return {"status": "success", "bots": bots}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update-retell-bot")
async def update_retell_bot_endpoint(request: Request):
    """
    Actualiza TODOS los campos del asistente (incluyendo voz) en Retell AI y PostgreSQL.
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
        asistente = data.get("asistente")

        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta agent_id")

        # Obtener información actual del agente
        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if not agent_info or "response_engine" not in agent_info:
            raise HTTPException(status_code=404, detail="Agente no encontrado")

        llm_id = agent_info["response_engine"].get("llm_id")
        current_voice_id = agent_info.get("voice_id")

        if not llm_id:
            raise HTTPException(status_code=400, detail="Agente sin LLM asociado")

        # Generar nuevo prompt mejorado
        nuevo_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email)

        # Actualizar prompt del LLM
        llm_update = retell_request("PATCH", f"/update-retell-llm/{llm_id}", {
            "general_prompt": nuevo_prompt
        })
        if not llm_update:
            raise HTTPException(status_code=500, detail="Error actualizando prompt en Retell AI")

        # Actualizar VOZ si ha cambiado
        if asistente:
            new_voice_id = VOICE_MAPPING.get(asistente)
            if new_voice_id and new_voice_id != current_voice_id:
                voice_update = retell_request("PATCH", f"/update-agent/{agent_id}", {
                    "voice_id": new_voice_id
                })
                if voice_update:
                    print(f"✅ Voz actualizada a {asistente}")

        # Actualizar en PostgreSQL
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE asistentes 
            SET nombre_negocio = %s, sector = %s, servicios = %s, 
                horario = %s, zona = %s, google_calendar_email = %s, asistente = %s
            WHERE agent_id = %s;
        """, (nombre_negocio, sector, servicios, horario, zona, calendar_email, asistente, agent_id))
        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "message": "Asistente actualizado completamente"}

    except Exception as e:
        print(f"❌ Error en update-retell-bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/delete-retell-bot")
async def delete_retell_bot_endpoint(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=400, detail="Falta agent_id")

        agent_info = retell_request("GET", f"/get-agent/{agent_id}")
        if agent_info:
            llm_id = agent_info.get("response_engine", {}).get("llm_id")

            # Liberar número
            numbers_res = retell_request("GET", "/v2/list-phone-numbers")
            if numbers_res and "items" in numbers_res:
                for phone in numbers_res["items"]:
                    if any(a.get("agent_id") == agent_id for a in phone.get("inbound_agents", [])):
                        retell_request("PATCH", f"/update-phone-number/{phone['phone_number']}", {
                            "inbound_agents": []
                        })

            retell_request("DELETE", f"/delete-agent/{agent_id}")
            if llm_id:
                retell_request("DELETE", f"/delete-retell-llm/{llm_id}")

        # Borrar de DB
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM asistentes WHERE agent_id = %s;", (agent_id,))
        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "message": "Asistente eliminado correctamente"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/book-appointment")
@app.post("/book-appointment/")
async def book_appointment(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        args = data.get("args", data)

        event = create_google_event(
            args.get("calendar_email"),
            args.get("summary"),
            args.get("start_time"),
            args.get("end_time"),
            args.get("description", "")
        )
        return {"code": "SUCCESS", "message": "Cita agendada correctamente"}
    except Exception as e:
        return {"code": "ERROR", "message": str(e)}

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
        return {"status": "success", "message": "Acceso verificado"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/create-retell-bot")
async def create_retell_bot_endpoint(request: Request):
    try:
        payload = await request.json()
        data = payload if isinstance(payload, dict) else payload.get("data", payload)
        voice_id = VOICE_MAPPING.get(data.get("asistente"), "openai-Alloy")
        return create_bot_for_client(
            data.get("nombre_negocio"),
            data.get("sector"),
            data.get("servicios"),
            data.get("horario"),
            data.get("zona"),
            voice_id,
            data.get("google_calendar_email")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"status": "Dansu Backend Completo y Mejorado - OK"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
