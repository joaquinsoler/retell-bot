import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import resend
from jose import JWTError, jwt
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ==================== TUS IMPORTS ORIGINALES ====================
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Dansu Backend Completo")

# ==================== VARIABLES DE ENTORNO ====================
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

if not all([RETELL_API_KEY, GOOGLE_CREDENTIALS_JSON, DATABASE_URL, JWT_SECRET_KEY, RESEND_API_KEY]):
    raise Exception("Faltan variables de entorno críticas")

# Configurar Resend
resend.api_key = RESEND_API_KEY

# ==================== JWT ====================
ALGORITHM = "HS256"
security = HTTPBearer()

def create_magic_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode = {"sub": email.lower().strip(), "exp": expire, "type": "magic"}
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

# ==================== MODELO ====================
class MagicLinkRequest(BaseModel):
    email: str

# ==================== ENDPOINT MAGIC LINK ====================
@app.post("/auth/magic-link")
async def send_magic_link(request: MagicLinkRequest):
    email = request.email.strip().lower()
    
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido")

    # Verificar que tenga asistentes
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as count FROM asistentes WHERE google_calendar_email = %s", (email,))
    result = cur.fetchone()
    cur.close()
    conn.close()

    if result['count'] == 0:
        return {"status": "success"}  # No revelamos información

    token = create_magic_token(email)
    login_url = f"https://www.dansu.info/area-cliente?token={token}"   # ← Cambia si tu URL exacta es distinta

    try:
        resend.Emails.send({
            "from": "Dansu <no-reply@dansu.info>",
            "to": email,
            "subject": "Tu enlace para acceder al panel de Dansu",
            "html": f"""
                <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 30px;">
                    <h2 style="color: #0078FF;">Bienvenido a Dansu</h2>
                    <p>Haz clic en el botón para entrar a tu panel:</p>
                    <a href="{login_url}" style="display: inline-block; background: #0078FF; color: white; padding: 16px 32px; border-radius: 8px; text-decoration: none; font-weight: bold;">
                        Entrar al Panel Dansu
                    </a>
                    <p style="color: #666; font-size: 14px; margin-top: 25px;">Este enlace caduca en 15 minutos por seguridad.</p>
                </div>
            """
        })
        return {"status": "success", "message": "Enlace enviado"}
    except Exception as e:
        print(f"❌ Error Resend: {e}")
        raise HTTPException(status_code=500, detail="Error al enviar el email")

# ==================== PROTEGER get-user-bots ====================
@app.post("/get-user-bots")
async def get_user_bots(user_email: str = Depends(get_current_user)):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (user_email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()
        return {"status": "success", "bots": bots}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
