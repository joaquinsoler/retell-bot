import os
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai

# Integración con Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from jose import JWTError, jwt

# ==================== CONFIGURACIÓN DE LOGS ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("DansuAI-Backend")

app = FastAPI(title="Dansu Backend Completo")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

# ==================== NUEVO ENDPOINT CHATBOT CRM ====================
@app.post("/ask-crm-bot")
async def ask_crm_bot(request: Request):
    try:
        data = await request.json()
        historial = data.get("historial", [])
        agent_id = data.get("agent_id")
        
        if not agent_id:
            return {"response": "Error: No se proporcionó un ID de asistente."}

        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT nombre_negocio, google_calendar_email, sector FROM asistentes WHERE agent_id = %s", (agent_id,))
        asistente = cur.fetchone()
        cur.close()
        conn.close()

        if not asistente:
            return {"response": "Error: No se pudo cargar la configuración de este asistente."}

        system_instruction = (
            f"Eres un experto en integraciones de CRM y Google Calendar para Dansu AI. "
            f"El negocio es '{asistente['nombre_negocio']}' del sector '{asistente['sector']}'. "
            f"Utilizan Google Calendar con el email: '{asistente['google_calendar_email']}'. "
            "Guía al usuario paso a paso para conectar su CRM con su calendario. "
            "Usa búsqueda en tiempo real para obtener documentación técnica actualizada. "
            "Sé conciso, amable y técnico."
        )

        gemini_history = []
        for msg in historial:
            gemini_history.append({"role": "user" if msg["role"] == "user" else "model", "parts": [msg["content"]]})
        
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(system_instruction)
        
        return {"response": response.text}
        
    except Exception as e:
        logger.error(f"Error en chatbot CRM: {e}")
        return {"response": "Lo siento, hubo un error técnico. Intenta de nuevo."}
# ==================== UTILIDADES DE SEGURIDAD Y GOOGLE ====================
def get_google_service(credentials_json_str):
    creds_dict = json.loads(credentials_json_str)
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    return build('calendar', 'v3', credentials=creds)

def create_magic_token(email: str):
    expire = datetime.utcnow() + timedelta(hours=24)
    to_encode = {"sub": email, "exp": expire}
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm="HS256")

def decode_magic_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        return payload.get("sub")
    except JWTError:
        return None

# ==================== INTEGRACIÓN BREVO (EMAILS) ====================
def send_magic_link_email(email, magic_link):
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }
    payload = {
        "sender": {"name": "Dansu AI", "email": "soporte@dansu.info"},
        "to": [{"email": email}],
        "subject": "Tu enlace mágico de acceso a Dansu",
        "htmlContent": f"<html><body><p>Hola,</p><p>Accede a tu panel aquí: <a href='{magic_link}'>Enlace Mágico</a></p></body></html>"
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.status_code == 201

# ==================== FUNCIONES DE NEGOCIO (DB) ====================
def create_bot_for_client(nombre, sector, servicios, horario, zona, voice_id, cal_email, idioma, datos_reserva, duracion):
    # Lógica de inserción en base de datos PostgreSQL
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    # Generar agent_id único si no existe, inserción...
    query = """
        INSERT INTO asistentes (nombre_negocio, sector, servicios, horario, zona, asistente, 
                                google_calendar_email, idioma, datos_reserva, duracion_cita)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    cur.execute(query, (nombre, sector, servicios, horario, zona, voice_id, cal_email, idioma, datos_reserva, duracion))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "success"}
# ==================== ENDPOINTS DE GESTIÓN DE ASISTENTES ====================

@app.get("/mis-asistentes")
async def obtener_asistentes(request: Request):
    # Autenticación mediante el token Bearer
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    
    token = auth_header.split(" ")[1]
    email = decode_magic_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    
    # Consulta a la base de datos
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM asistentes WHERE owner_email = %s", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    
    return {"asistentes": bots}

@app.post("/update-retell-bot")
async def actualizar_bot(request: Request):
    data = await request.json()
    agent_id = data.get("agent_id")
    
    # Aquí iría la lógica de llamada a la API de Retell AI
    # para actualizar los parámetros en el servidor de Retell
    retell_url = f"https://api.retellai.com/v2/update-agent/{agent_id}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}"}
    
    response = requests.patch(retell_url, json=data, headers=headers)
    
    if response.status_code == 200:
        # Si Retell acepta el cambio, actualizamos nuestra base de datos local
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        query = """
            UPDATE asistentes SET nombre_negocio=%s, sector=%s, servicios=%s, 
            horario=%s, duracion_cita=%s, zona=%s, idioma=%s, datos_reserva=%s, asistente=%s
            WHERE agent_id=%s
        """
        cur.execute(query, (data['nombre_negocio'], data['sector'], data['servicios'], 
                            data['horario'], data['duracion_cita'], data['zona'], 
                            data['idioma'], data['datos_reserva'], data['asistente'], agent_id))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "success"}
    
    return {"status": "error", "detail": "No se pudo actualizar en Retell AI"}

@app.post("/delete-retell-bot")
async def eliminar_bot(request: Request):
    data = await request.json()
    agent_id = data.get("agent_id")
    
    # Lógica para eliminar tanto en Retell como en PostgreSQL
    # ... (implementación de DELETE en ambas plataformas)
    return {"status": "success"}
# ==================== AUTH Y MAGIC LINK ====================

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    data = await request.json()
    email = data.get("email")
    
    # 1. Verificar si el usuario existe en tu base de datos
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    # Consulta corregida:
cur.execute("SELECT google_calendar_email FROM asistentes WHERE google_calendar_email = %s LIMIT 1", (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    
    if user:
        token = create_magic_token(email)
        magic_link = f"https://tuweb.com/dashboard?token={token}"
        send_magic_link_email(email, magic_link)
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

@app.get("/check-session")
async def check_session(request: Request):
    # Lógica para validar que el token del usuario es correcto
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return {"status": "error"}
    
    token = auth_header.split(" ")[1]
    email = decode_magic_token(token)
    
    if email:
        # Si el token es válido, devolvemos los datos del usuario
        return {"status": "success", "email": email, "bots": []} # Aquí inyectarías la lista de bots
    return {"status": "error"}

# ==================== ARRANQUE DEL SERVIDOR ====================
if __name__ == "__main__":
    import uvicorn
    # En Render, se utiliza el puerto asignado por la variable de entorno
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
