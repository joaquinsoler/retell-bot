import os
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import psycopg2
import json
import csv
import io

# ======================
# LOGGING
# ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("dansu-saas")

app = FastAPI(title="Dansu SaaS - Native Google OAuth & Multi-Assistant")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# VARIABLES DE ENTORNO
# ======================
APIDECK_API_KEY = os.getenv("APIDECK_API_KEY")
APIDECK_APP_ID = os.getenv("APIDECK_APP_ID")
APIDECK_BASE = "https://unify.apideck.com"

# Google OAuth Nativo
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://retell-bot.onrender.com/auth/google/callback")

DATABASE_URL = os.getenv("DATABASE_URL")

# ======================
# BASE DE DATOS
# ======================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                google_connection_id VARCHAR(255),
                apideck_consumer_id VARCHAR(255),
                crm_provider VARCHAR(100),
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS assistants (
                id SERIAL PRIMARY KEY,
                assistant_id VARCHAR(255) UNIQUE NOT NULL,
                email VARCHAR(255) NOT NULL REFERENCES users(email) ON DELETE CASCADE,
                assistant_name VARCHAR(255),
                crm_schema_data JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

        conn.commit()
        cur.close()
        conn.close()
        logger.info("Base de datos y tablas inicializadas correctamente.")
    except Exception as e:
        logger.error(f"Error creando tablas en base de datos: {e}")

init_db()

# ======================
# MODELOS PYDANTIC
# ======================
class ApideckSessionRequest(BaseModel):
    consumer_id: str
    user_name: Optional[str] = None
    account_name: Optional[str] = None

# ======================
# HELPERS
# ======================
def apideck_headers(consumer_id: str) -> dict:
    return {
        "Authorization": f"Bearer {APIDECK_API_KEY}",
        "x-apideck-app-id": APIDECK_APP_ID,
        "x-apideck-consumer-id": consumer_id,
        "Content-Type": "application/json",
    }

# ======================
# 1. GOOGLE OAUTH NATIVO
# ======================
@app.get("/auth/google/login")
async def google_login():
    """Redirige al usuario a la pantalla oficial de inicio de sesión de Google."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="Faltan variables de entorno de Google")

    google_auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={GOOGLE_CLIENT_ID}&"
        f"redirect_uri={GOOGLE_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=openid%20email%20profile&"
        f"access_type=offline&"
        f"prompt=consent"
    )
    return RedirectResponse(url=google_auth_url)

@app.get("/auth/google/callback")
async def google_callback(code: str = None, error: str = None):
    """Recibe el código de Google, obtiene el token y el perfil, y registra/actualiza el usuario por email."""
    if error:
        raise HTTPException(status_code=400, detail=f"Error de Google: {error}")

    if not code:
        raise HTTPException(status_code=400, detail="No se recibió el código de autorización de Google")

    try:
        # 1. Intercambiar el código por tokens de acceso
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
        token_res = requests.post(token_url, data=token_data, timeout=15)
        if token_res.status_code != 200:
            logger.error(f"Error token Google: {token_res.text}")
            raise HTTPException(status_code=500, detail=f"Error obteniendo token de Google: {token_res.text}")
        
        token_json = token_res.json()
        access_token = token_json.get("access_token")

        # 2. Obtener la información del usuario (email)
        user_info_url = "https://www.googleapis.com/oauth2/v2/userinfo"
        user_res = requests.get(user_info_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        if user_res.status_code != 200:
            raise HTTPException(status_code=500, detail="Error obteniendo perfil de Google")
        
        user_info = user_res.json()
        email = user_info.get("email")

        if not email:
            raise HTTPException(status_code=400, detail="No se pudo obtener el email de la cuenta de Google")

        # 3. Guardar o actualizar en base de datos
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (email, apideck_consumer_id)
            VALUES (%s, %s)
            ON CONFLICT (email) DO UPDATE 
            SET apideck_consumer_id = EXCLUDED.apideck_consumer_id
        """, (email, email))
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"Usuario autenticado de forma nativa por Gmail: {email}")

        # 4. Redirigir al frontend
        frontend_redirect_url = f"https://dansu.info?email={email}&auth=success"
        return RedirectResponse(url=frontend_redirect_url)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en el callback de Google OAuth: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ======================
# 2. CRM APIDECK
# ======================
@app.post("/apideck/session")
async def create_vault_session(body: ApideckSessionRequest):
    redirect_uri = "https://dansu.info"
    
    payload = {
        "redirect_uri": redirect_uri,
        "consumer_metadata": {
            "account_name": body.account_name or "Cliente Dansu",
            "user_name": body.user_name or body.consumer_id,
        },
        "settings": {
            "unified_apis": ["crm"],
            "auto_redirect": True,
            "isolation_mode": True,
            "hide_guides": True,
        },
    }

    try:
        response = requests.post(
            f"{APIDECK_BASE}/vault/sessions",
            headers=apideck_headers(body.consumer_id),
            json=payload,
            timeout=15,
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)

        data = response.json().get("data", {})
        return {
            "success": True,
            "session_uri": data.get("session_uri"),
            "session_token": data.get("session_token"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/apideck/connection-status/{consumer_id}")
async def check_crm_connection(consumer_id: str):
    """Verifica el estado de conexión con los CRMs compatibles en Apideck."""
    for service_id in ["hubspot", "salesforce", "zoho"]:
        try:
            response = requests.get(
                f"{APIDECK_BASE}/vault/connections/crm/{service_id}",
                headers=apideck_headers(consumer_id),
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json().get("data", {})
                state = data.get("state")
                enabled = data.get("enabled", False)
                if state == "callable" and enabled:
                    return {"connected": True, "service": service_id, "state": state}
        except Exception:
            continue
            
    return {"connected": False}

# ======================
# 3. SINCRONIZACIÓN Y ASISTENTES
# ======================
@app.get("/apideck/sync-schema/{consumer_id}")
async def sync_crm_schema_to_logs(consumer_id: str):
    logger.info(f"=== INICIANDO SINCRONIZACIÓN DE ASISTENTE PARA: {consumer_id} ===")

    user_email = consumer_id

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (email, apideck_consumer_id)
            VALUES (%s, %s)
            ON CONFLICT (email) DO NOTHING
        """, (user_email, user_email))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as db_err:
        logger.error(f"Error asegurando usuario por email en BD: {db_err}")

    # Detectar servicio CRM activo
    active_service = "hubspot"
    for service_id in ["hubspot", "salesforce", "zoho", "pipedrive"]:
        try:
            check_res = requests.get(
                f"{APIDECK_BASE}/vault/connections/crm/{service_id}",
                headers=apideck_headers(consumer_id),
                timeout=5,
            )
            if check_res.status_code == 200:
                data = check_res.json().get("data", {})
                if data.get("state") == "callable" and data.get("enabled", False):
                    active_service = service_id
                    break
        except Exception:
            continue

    headers_crm = {**apideck_headers(consumer_id), "x-apideck-service-id": active_service}
    resources = ["contacts", "companies", "opportunities", "leads"]
    schema_results = {}

    for resource in resources:
        try:
            res = requests.get(f"{APIDECK_BASE}/crm/{resource}", headers=headers_crm, params={"limit": 1}, timeout=10)
            if res.status_code < 400:
                schema_results[resource] = res.json()
            else:
                schema_results[resource] = {"status": res.status_code, "error": res.text}
        except Exception as err:
            schema_results[resource] = {"error": str(err)}

    assistant_id = f"ast-{os.urandom(4).hex()}"
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO assistants (assistant_id, email, assistant_name, crm_schema_data)
            VALUES (%s, %s, %s, %s)
        """, (assistant_id, user_email, f"Asistente {active_service.capitalize()} - {assistant_id[:6]}", json.dumps(schema_results)))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Nuevo asistente {assistant_id} ({active_service}) creado y vinculado al correo: {user_email}")
    except Exception as db_err:
        logger.error(f"Error guardando nuevo asistente en BD: {db_err}")

    csv_buffer = io.StringIO()
    csv_writer = csv.writer(csv_buffer, delimiter=';')
    csv_writer.writerow(["email", "assistant_id", "crm_provider", "recurso", "estado"])
    
    for resource in resources:
        status_text = "Disponible / Estructurado OK" if "error" not in str(schema_results[resource]) else "Error de esquema"
        csv_writer.writerow([user_email, assistant_id, active_service, resource, status_text])

    csv_output = csv_buffer.getvalue().strip()
    logger.info(f"\n==================================================\n✅ ASISTENTE CREADO ({active_service})\n{csv_output}\n==================================================")

    return {
        "success": True,
        "message": f"Nuevo asistente creado con éxito para el usuario usando {active_service}.",
        "assistant_id": assistant_id,
        "crm_provider": active_service
    }

@app.get("/")
async def root():
    return {"status": "ok", "service": "dansu-saas-native-google"}
