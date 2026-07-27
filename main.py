import os
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
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

app = FastAPI(title="Dansu SaaS - Multi-Assistant Architecture")

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

NANGO_API_KEY = os.getenv("NANGO_API_KEY")
NANGO_API_URL = "https://api.nango.dev"
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
                user_id VARCHAR(255) UNIQUE NOT NULL,
                email VARCHAR(255),
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
                user_id VARCHAR(255) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                assistant_name VARCHAR(255),
                crm_schema_data JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

        conn.commit()
        cur.close()
        conn.close()
        logger.info("Base de datos y tablas ('users', 'assistants') inicializadas correctamente.")
    except Exception as e:
        logger.error(f"Error creando tablas en base de datos: {e}")

init_db()

# ======================
# MODELOS PYDANTIC
# ======================
class SessionRequest(BaseModel):
    userId: Optional[str] = None
    email: Optional[str] = None

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
# 1. GOOGLE OAUTH (NANGO)
# ======================
@app.post("/session-token")
async def create_nango_session_token(body: SessionRequest):
    try:
        headers = {
            "Authorization": f"Bearer {NANGO_API_KEY}",
            "Content-Type": "application/json"
        }
        user_id = body.userId or f"user-{os.urandom(4).hex()}"
        tags = {"end_user_id": user_id}
        if body.email:
            tags["end_user_email"] = body.email

        payload = {
            "allowed_integrations": ["google"],
            "tags": tags
        }

        response = requests.post(
            f"{NANGO_API_URL}/connect/sessions",
            headers=headers,
            json=payload,
            timeout=15
        )

        if response.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=response.text)

        data = response.json()
        token = data.get("data", {}).get("token") or data.get("token")
        if not token:
            raise HTTPException(status_code=500, detail="No se recibió token de Nango")

        return {"sessionToken": token, "userId": user_id}

    except Exception as e:
        logger.error(f"Error creando session token de Nango: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/nango-webhook")
async def nango_webhook(request: Request):
    try:
        payload = await request.json()
        if (
            payload.get("type") == "auth"
            and payload.get("operation") == "creation"
            and payload.get("success") is True
        ):
            connection_id = payload.get("connectionId")
            tags = payload.get("tags") or {}
            user_id = tags.get("end_user_id")
            
            email = tags.get("end_user_email")
            if not email:
                connection_config = payload.get("connectionConfig") or {}
                end_user = connection_config.get("end_user") or {}
                email = end_user.get("email") or payload.get("endUserEmail") or "usuario_autenticado@google.com"

            if user_id:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO users (user_id, email, google_connection_id, apideck_consumer_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE 
                    SET google_connection_id = EXCLUDED.google_connection_id,
                        email = COALESCE(EXCLUDED.email, users.email),
                        apideck_consumer_id = EXCLUDED.apideck_consumer_id
                """, (user_id, email, connection_id, user_id))
                conn.commit()
                cur.close()
                conn.close()
                logger.info(f"Usuario registrado/actualizado en BD: {user_id}")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error en webhook de Nango: {e}")
        return {"status": "error", "message": str(e)}

# ======================
# 2. CRM APIDECK (HUBSPOT)
# ======================
@app.post("/apideck/session")
async def create_vault_session(body: ApideckSessionRequest):
    # REDIRECCIÓN CONFIGURADA A dansu.info
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
async def check_hubspot_connection(consumer_id: str):
    try:
        response = requests.get(
            f"{APIDECK_BASE}/vault/connections/crm/hubspot",
            headers=apideck_headers(consumer_id),
            timeout=10,
        )
        if response.status_code == 404:
            return {"connected": False}

        data = response.json().get("data", {})
        state = data.get("state")
        enabled = data.get("enabled", False)
        return {"connected": (state == "callable" and enabled), "state": state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ======================
# 3. SINCRONIZACIÓN Y LOGS CSV
# ======================
@app.get("/apideck/sync-schema/{consumer_id}")
async def sync_crm_schema_to_logs(consumer_id: str):
    logger.info(f"=== INICIANDO PROCESO UNIFICADO PARA CONSUMER: {consumer_id} ===")

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO users (user_id, email, apideck_consumer_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
        """, (consumer_id, "usuario_temporal@google.com", consumer_id))
        
        cur.execute("SELECT email FROM users WHERE user_id = %s", (consumer_id,))
        row = cur.fetchone()
        user_email = row[0] if row else "Sin email"
        
        conn.commit()
        cur.close()
        conn.close()
    except Exception as db_err:
        logger.error(f"Error asegurando usuario en BD: {db_err}")

    headers_hubspot = {**apideck_headers(consumer_id), "x-apideck-service-id": "hubspot"}
    resources = ["contacts", "companies", "opportunities", "leads"]
    schema_results = {}

    for resource in resources:
        try:
            res = requests.get(f"{APIDECK_BASE}/crm/{resource}", headers=headers_hubspot, params={"limit": 1}, timeout=10)
            if res.status_code < 400:
                schema_results[resource] = res.json()
            else:
                schema_results[resource] = {"status": res.status_code, "error": res.text}
        except Exception as err:
            schema_results[resource] = {"error": str(err)}

    assistant_id = f"ast-{consumer_id}"
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO assistants (assistant_id, user_id, assistant_name, crm_schema_data)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (assistant_id) DO UPDATE 
            SET crm_schema_data = EXCLUDED.crm_schema_data
        """, (assistant_id, consumer_id, "Asistente Principal HubSpot", json.dumps(schema_results)))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Esquema CRM guardado en la tabla 'assistants' para el asistente: {assistant_id}")
    except Exception as db_err:
        logger.error(f"Error guardando esquema en assistants: {db_err}")

    csv_buffer = io.StringIO()
    csv_writer = csv.writer(csv_buffer, delimiter=';')
    csv_writer.writerow(["user_id", "email", "assistant_id", "crm_provider", "recurso", "estado"])
    
    for resource in resources:
        status_text = "Disponible / Estructurado OK" if "error" not in str(schema_results[resource]) else "Error de esquema"
        csv_writer.writerow([consumer_id, user_email, assistant_id, "hubspot", resource, status_text])

    csv_output = csv_buffer.getvalue().strip()

    logger.info(
        f"\n==================================================\n"
        f"✅ ÉXITO: ASISTENTE CREADO Y ESQUEMA CRM ALMACENADO\n"
        f"--------------------------------------------------\n"
        f"{csv_output}\n"
        f"=================================================="
    )

    return {
        "success": True,
        "message": "Esquema sincronizado y guardado en la tabla assistants correctamente.",
        "assistant_id": assistant_id
    }

@app.get("/")
async def root():
    return {"status": "ok", "service": "dansu-saas-multi-assistant"}
