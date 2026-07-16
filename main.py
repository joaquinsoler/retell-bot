import os
import json
import logging
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from jose import JWTError, jwt

# Configuración
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("DansuAI-Backend")

app = FastAPI(title="Dansu Backend Unificado V3.6")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Variables
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
@app.post("/ask-crm-bot")
async def ask_crm_bot(request: Request):
    try:
        data = await request.json()
        historial = data.get("historial", [])
        agent_id = data.get("agent_id")
        
        if not agent_id:
            return {"response": "Error: No se proporcionó ID."}

        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT nombre_negocio, google_calendar_email, sector FROM asistentes WHERE agent_id = %s", (agent_id,))
        asistente = cur.fetchone()
        cur.close()
        conn.close()

        if not asistente:
            return {"response": "No se encontró el asistente configurado."}

        system_instruction = (
            f"Eres el soporte experto de Dansu AI para '{asistente['nombre_negocio']}' ({asistente['sector']}). "
            f"El calendario vinculado es: '{asistente['google_calendar_email']}'. "
            "Ayuda al usuario a conectar el CRM. Sé breve, técnico y amable."
        )

        gemini_history = [{"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]} for m in historial]
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(system_instruction)
        return {"response": response.text}
        
    except Exception as e:
        logger.error(f"Error chatbot: {e}")
        return {"response": "Error técnico en el chat. Intenta de nuevo."}
# ==================== ENDPOINTS DE GESTIÓN DE ASISTENTES ====================

@app.get("/mis-asistentes")
async def obtener_asistentes(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    
    token = auth_header.split(" ")[1]
    email = decode_magic_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # Corregido: Filtramos por google_calendar_email en lugar de owner_email
    cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s", (email,))
    bots = cur.fetchall()
    cur.close()
    conn.close()
    
    return {"asistentes": bots}

@app.post("/update-retell-bot")
async def actualizar_bot(request: Request):
    data = await request.json()
    agent_id = data.get("agent_id")
    
    retell_url = f"https://api.retellai.com/v2/update-agent/{agent_id}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}"}
    
    response = requests.patch(retell_url, json=data, headers=headers)
    
    if response.status_code == 200:
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
    # Lógica de eliminación...
    return {"status": "success"}
# ==================== AUTH Y MAGIC LINK ====================

@app.post("/request-magic-link")
async def request_magic_link(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        
        # CORRECCIÓN: Buscamos en la tabla que existe (asistentes)
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT google_calendar_email FROM asistentes WHERE google_calendar_email = %s LIMIT 1", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user:
            token = create_magic_token(email)
            # Asegúrate de que esta URL sea la correcta para tu despliegue
            magic_link = f"https://retell-bot.onrender.com/login?token={token}"
            send_magic_link_email(email, magic_link)
            return {"status": "success"}
        else:
            raise HTTPException(status_code=404, detail="Usuario no registrado en asistentes")
    except Exception as e:
        logger.error(f"Error en magic link: {e}")
        raise HTTPException(status_code=500, detail="Error interno")

@app.get("/check-session")
async def check_session(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header: return {"status": "error"}
    token = auth_header.split(" ")[1]
    email = decode_magic_token(token)
    return {"status": "success", "email": email} if email else {"status": "error"}

# ==================== ARRANQUE DEL SERVIDOR ====================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
