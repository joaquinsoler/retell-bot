import os
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import psycopg2
import json

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
# BASE DE DATOS (EMAIL COMO IDENTIFICADOR ÚNICO)
# ======================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 1. Tabla de Usuarios: El email es la clave única (evita duplicados si el usuario ya existe)
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

        # 2. Tabla de Asistentes: Relación 1 a N basada en el email del usuario (permite múltiples asistentes por cuenta)
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
        user_identifier = body.email or body.userId or f"user-{os.urandom(4).hex()}"
        tags = {"end_user_id": user_identifier}
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

        return {"sessionToken": token, "userId": user_identifier}

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
            
            # Extraer el email real devuelto por la autenticación de Google en Nango
            email = tags.get("end_user_email")
            if not email:
                connection_config = payload.get("connectionConfig") or {}
                end_user = connection_config.get("end_user") or {}
                email = end_user.get("email") or payload.get("endUserEmail")

            if not email:
                auth_details = payload.get("auth") or {}
                email = auth_details.get("email") or payload.get("connectionConfig", {}).get("email")

            if email:
                conn = get_db_connection()
                cur = conn.cursor()
                # Upsert estricto basado en el EMAIL: si ya existe, no crea usuario nuevo, actualiza su conexión
                cur.execute("""
                    INSERT INTO users (email, google_connection_id, apideck_consumer_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (email) DO UPDATE 
                    SET google_connection_id = EXCLUDED.google_connection_id,
                        apideck_consumer_id = EXCLUDED.apideck_consumer_id
                """, (email, connection_id, email))
                conn.commit()
                cur.close()
                conn.close()
                logger.info(f"Usuario autenticado por Gmail guardado/actualizado (sin duplicar): {email}")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error en webhook de Nango: {e}")
        return {"status": "error", "message": str(e)}
# ======================
# 2. CRM APIDECK (HUBSPOT)
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
# 3. SINCRONIZACIÓN Y CREACIÓN DE NUEVOS ASISTENTES (RELACIÓN 1 A N)
# ======================
@app.get("/apideck/sync-schema/{consumer_id}")
async def sync_crm_schema_to_logs(consumer_id: str):
    logger.info(f"=== INICIANDO SINCRONIZACIÓN Y CREACIÓN DE ASISTENTE PARA: {consumer_id} ===")

    user_email = consumer_id

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Garantizar que el usuario base existe por su email
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

    # Generar un identificador completamente único para permitir múltiples asistentes por el mismo usuario
    assistant_id = f"ast-{os.urandom(4).hex()}"
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Inserta un nuevo registro de asistente asociado al email del usuario (relación 1 a N)
        cur.execute("""
            INSERT INTO assistants (assistant_id, email, assistant_name, crm_schema_data)
            VALUES (%s, %s, %s, %s)
        """, (assistant_id, user_email, f"Asistente HubSpot - {assistant_id[:6]}", json.dumps(schema_results)))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Nuevo asistente único ({assistant_id}) creado y vinculado correctamente al usuario: {user_email}")
    except Exception as db_err:
        logger.error(f"Error guardando nuevo asistente en BD: {db_err}")

    # Generar salida CSV para logs
    csv_buffer = io.StringIO()
    csv_writer = csv.writer(csv_buffer, delimiter=';')
    csv_writer.writerow(["email", "assistant_id", "crm_provider", "recurso", "estado"])
    
    for resource in resources:
        status_text = "Disponible / Estructurado OK" if "error" not in str(schema_results[resource]) else "Error de esquema"
        csv_writer.writerow([user_email, assistant_id, "hubspot", resource, status_text])

    csv_output = csv_buffer.getvalue().strip()

    logger.info(
        f"\n==================================================\n"
        f"✅ ÉXITO: NUEVO ASISTENTE CREADO PARA EL USUARIO\n"
        f"--------------------------------------------------\n"
        f"{csv_output}\n"
        f"=================================================="
    )

    return {
        "success": True,
        "message": "Nuevo asistente creado con éxito y vinculado al usuario por email.",
        "assistant_id": assistant_id
    }

@app.get("/")
async def root():
    return {"status": "ok", "service": "dansu-saas-multi-assistant"}
