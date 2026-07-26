import os
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

# ======================
# LOGGING REFORZADO
# ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("apideck")

app = FastAPI(title="Retell Bot - Apideck HubSpot")

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

if not APIDECK_API_KEY or not APIDECK_APP_ID:
    logger.error("FALTAN variables de entorno APIDECK_API_KEY o APIDECK_APP_ID")
else:
    logger.info("Variables de entorno Apideck cargadas correctamente")


# ======================
# MODELOS
# ======================
class CreateSessionRequest(BaseModel):
    consumer_id: str
    redirect_uri: Optional[str] = None
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
# 1. CREAR SESIÓN DE VAULT
# ======================
@app.post("/apideck/session")
async def create_vault_session(body: CreateSessionRequest):
    """
    Crea una sesión de Apideck Vault para que el cliente conecte HubSpot.
    """
    logger.info(f"Solicitud de sesión recibida | consumer_id={body.consumer_id}")

    if not APIDECK_API_KEY or not APIDECK_APP_ID:
        logger.error("Apideck no está configurado (faltan API_KEY o APP_ID)")
        raise HTTPException(status_code=500, detail="Apideck no configurado en el servidor")

    # URL de retorno por defecto (cámbiala si quieres otra)
    redirect_uri = body.redirect_uri or "https://retell-bot.onrender.com"

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
        logger.info(f"Creando sesión Vault para consumer_id={body.consumer_id} ...")
        response = requests.post(
            f"{APIDECK_BASE}/vault/sessions",
            headers=apideck_headers(body.consumer_id),
            json=payload,
            timeout=15,
        )

        logger.info(f"Respuesta Apideck status={response.status_code}")

        if response.status_code >= 400:
            logger.error(f"Error de Apideck: {response.text}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Error de Apideck: {response.text}"
            )

        data = response.json().get("data", {})
        session_uri = data.get("session_uri")
        session_token = data.get("session_token")

        if not session_uri:
            logger.error("Apideck no devolvió session_uri")
            raise HTTPException(status_code=500, detail="No se recibió session_uri de Apideck")

        logger.info(f"Sesión Vault creada correctamente | consumer_id={body.consumer_id}")
        logger.info(f"session_uri = {session_uri}")

        return {
            "success": True,
            "consumer_id": body.consumer_id,
            "session_uri": session_uri,
            "session_token": session_token,
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Error de red al crear sesión: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error de conexión con Apideck: {str(e)}")


# ======================
# 2. COMPROBAR ESTADO DE CONEXIÓN
# ======================
@app.get("/apideck/connection-status/{consumer_id}")
async def check_hubspot_connection(consumer_id: str):
    """
    Comprueba si HubSpot está conectado y en estado 'callable'.
    """
    logger.info(f"Comprobando conexión HubSpot | consumer_id={consumer_id}")

    try:
        response = requests.get(
            f"{APIDECK_BASE}/vault/connections/crm/hubspot",
            headers=apideck_headers(consumer_id),
            timeout=10,
        )

        if response.status_code == 404:
            logger.info(f"[{consumer_id}] HubSpot NO está conectado (404)")
            return {
                "connected": False,
                "state": None,
                "message": "HubSpot no conectado"
            }

        if response.status_code >= 400:
            logger.error(f"[{consumer_id}] Error al comprobar conexión: {response.text}")
            raise HTTPException(status_code=response.status_code, detail=response.text)

        data = response.json().get("data", {})
        state = data.get("state")
        enabled = data.get("enabled", False)

        is_connected = state == "callable" and enabled

        if is_connected:
            logger.info(f"[{consumer_id}] HubSpot CONECTADO correctamente (state=callable)")
        else:
            logger.info(f"[{consumer_id}] HubSpot existe pero state={state}")

        return {
            "connected": is_connected,
            "state": state,
            "enabled": enabled,
            "service_id": data.get("service_id"),
            "name": data.get("name"),
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Error de red al comprobar conexión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
# ======================
# 3. ESTRUCTURA DE LA API (para Retell)
# ======================
@app.get("/apideck/crm-schema/{consumer_id}")
async def get_crm_schema(consumer_id: str):
    """
    Devuelve la estructura de recursos y campos principales
    que el asistente de Retell puede usar.
    """
    logger.info(f"[{consumer_id}] Solicitando schema del CRM")

    # Comprobamos primero que esté conectado
    status_response = await check_hubspot_connection(consumer_id)
    if not status_response.get("connected"):
        logger.warning(f"[{consumer_id}] Intentó pedir schema sin tener HubSpot conectado")
        raise HTTPException(
            status_code=400,
            detail="HubSpot no está conectado para este consumer_id"
        )

    schema = {
        "provider": "hubspot",
        "via": "apideck",
        "unified_api": "crm",
        "base_url": "https://unify.apideck.com/crm",
        "resources": {
            "contacts": {
                "description": "Contactos / Personas",
                "endpoints": {
                    "list": "GET /crm/contacts",
                    "get": "GET /crm/contacts/{id}",
                    "create": "POST /crm/contacts",
                    "update": "PATCH /crm/contacts/{id}",
                },
                "main_fields": [
                    "id", "first_name", "last_name", "name", "emails",
                    "phone_numbers", "company_id", "title", "owner_id",
                    "created_at", "updated_at"
                ],
            },
            "companies": {
                "description": "Empresas",
                "endpoints": {
                    "list": "GET /crm/companies",
                    "get": "GET /crm/companies/{id}",
                    "create": "POST /crm/companies",
                    "update": "PATCH /crm/companies/{id}",
                },
                "main_fields": [
                    "id", "name", "website", "phone_numbers", "emails",
                    "industry", "owner_id", "created_at", "updated_at"
                ],
            },
            "opportunities": {
                "description": "Oportunidades / Deals",
                "endpoints": {
                    "list": "GET /crm/opportunities",
                    "get": "GET /crm/opportunities/{id}",
                    "create": "POST /crm/opportunities",
                    "update": "PATCH /crm/opportunities/{id}",
                },
                "main_fields": [
                    "id", "title", "description", "amount", "currency",
                    "stage", "pipeline_id", "status", "contact_id",
                    "company_id", "owner_id", "close_date",
                    "created_at", "updated_at"
                ],
            },
            "activities": {
                "description": "Actividades (llamadas, emails, reuniones, tareas)",
                "endpoints": {
                    "list": "GET /crm/activities",
                    "get": "GET /crm/activities/{id}",
                    "create": "POST /crm/activities",
                },
                "main_fields": [
                    "id", "type", "title", "description", "duration_seconds",
                    "start_datetime", "end_datetime", "contact_id",
                    "company_id", "opportunity_id", "owner_id", "created_at"
                ],
            },
            "notes": {
                "description": "Notas",
                "endpoints": {
                    "list": "GET /crm/notes",
                    "create": "POST /crm/notes",
                },
                "main_fields": [
                    "id", "title", "content", "contact_id",
                    "company_id", "opportunity_id"
                ],
            },
            "users": {
                "description": "Usuarios / Owners del CRM",
                "endpoints": {
                    "list": "GET /crm/users",
                },
                "main_fields": [
                    "id", "first_name", "last_name", "email", "status"
                ],
            },
        },
        "headers_required": {
            "Authorization": "Bearer {APIDECK_API_KEY}",
            "x-apideck-app-id": "{APIDECK_APP_ID}",
            "x-apideck-consumer-id": "{consumer_id}",
            "x-apideck-service-id": "hubspot",
        },
        "notes_for_retell": [
            "Siempre incluir el header x-apideck-service-id: hubspot",
            "Usar el consumer_id del cliente en todas las peticiones",
            "Los campos están unificados por Apideck (no son los nombres originales de HubSpot)",
            "Para crear una actividad de tipo llamada usar type='call'",
        ],
    }

    logger.info(f"[{consumer_id}] Schema CRM devuelto correctamente")
    return schema


# ======================
# HEALTH CHECK
# ======================
@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "retell-bot-apideck",
        "apideck_configured": bool(APIDECK_API_KEY and APIDECK_APP_ID)
    }
