import os
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

# Configuración de logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Retell Bot - Apideck HubSpot")

# CORS (necesario para el frontend HTML)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción pon solo tu dominio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# VARIABLES DE ENTORNO
# ======================
APIDECK_API_KEY = os.getenv("APIDECK_API_KEY")          # sk_live_...
APIDECK_APP_ID = os.getenv("APIDECK_APP_ID")            # BtPS5QsuQzTziOUpoC8NBPyfv87x382r9XpPoC9I
APIDECK_BASE = "https://unify.apideck.com"

if not APIDECK_API_KEY or not APIDECK_APP_ID:
    logger.warning("Faltan APIDECK_API_KEY o APIDECK_APP_ID en las variables de entorno")


# ======================
# MODELOS
# ======================
class CreateSessionRequest(BaseModel):
    consumer_id: str                    # ID único del cliente (ej: user-123 o el id de tu BD)
    redirect_uri: Optional[str] = None  # A dónde volver después de conectar
    user_name: Optional[str] = None
    account_name: Optional[str] = None


# ======================
# HELPERS
# ======================
def apideck_headers(consumer_id: str):
    return {
        "Authorization": f"Bearer {APIDECK_API_KEY}",
        "x-apideck-app-id": APIDECK_APP_ID,
        "x-apideck-consumer-id": consumer_id,
        "Content-Type": "application/json",
    }


# ======================
# 1. CREAR SESIÓN DE VAULT (para que el cliente conecte HubSpot)
# ======================
@app.post("/apideck/session")
async def create_vault_session(body: CreateSessionRequest):
    """
    El frontend llama a este endpoint.
    Devuelve una session_uri para que el usuario vaya a conectar HubSpot.
    """
    if not APIDECK_API_KEY or not APIDECK_APP_ID:
        raise HTTPException(status_code=500, detail="Apideck no configurado")

    payload = {
        "redirect_uri": body.redirect_uri or "https://retell-bot.onrender.com/connected",
        "consumer_metadata": {
            "account_name": body.account_name or "Cliente",
            "user_name": body.user_name or body.consumer_id,
        },
        "settings": {
            "unified_apis": ["crm"],          # Solo mostramos CRM
            "auto_redirect": True,
            "isolation_mode": True,           # Experiencia más limpia
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
        response.raise_for_status()
        data = response.json().get("data", {})

        session_uri = data.get("session_uri")
        session_token = data.get("session_token")

        logger.info(f"Sesión Vault creada para consumer_id={body.consumer_id}")

        return {
            "success": True,
            "consumer_id": body.consumer_id,
            "session_uri": session_uri,       # ← Abre esta URL en el navegador del cliente
            "session_token": session_token,   # ← Si quieres usar Vault JS embebido
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Error creando sesión Vault: {e}")
        raise HTTPException(status_code=500, detail=f"Error creando sesión: {str(e)}")


# ======================
# 2. COMPROBAR SI HUBSPOT ESTÁ CONECTADO
# ======================
@app.get("/apideck/connection-status/{consumer_id}")
async def check_hubspot_connection(consumer_id: str):
    """
    Comprueba si el cliente ya tiene HubSpot conectado y en estado 'callable'.
    Escribe en los logs el resultado.
    """
    try:
        response = requests.get(
            f"{APIDECK_BASE}/vault/connections/crm/hubspot",
            headers=apideck_headers(consumer_id),
            timeout=10,
        )

        if response.status_code == 404:
            logger.info(f"[{consumer_id}] HubSpot NO está conectado")
            return {"connected": False, "state": None, "message": "HubSpot no conectado"}

        response.raise_for_status()
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
        logger.error(f"Error comprobando conexión: {e}")
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
    # Primero comprobamos que esté conectado
    status = await check_hubspot_connection(consumer_id)
    if not status.get("connected"):
        raise HTTPException(status_code=400, detail="HubSpot no está conectado para este consumer")

    # Estructura unificada de Apideck CRM (la que realmente usará Retell)
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
                    "id", "first_name", "last_name", "name", "emails", "phone_numbers",
                    "company_id", "title", "owner_id", "created_at", "updated_at"
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
                    "stage", "pipeline_id", "status", "contact_id", "company_id",
                    "owner_id", "close_date", "created_at", "updated_at"
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
                    "start_datetime", "end_datetime", "contact_id", "company_id",
                    "opportunity_id", "owner_id", "created_at"
                ],
            },
            "notes": {
                "description": "Notas",
                "endpoints": {
                    "list": "GET /crm/notes",
                    "create": "POST /crm/notes",
                },
                "main_fields": ["id", "title", "content", "contact_id", "company_id", "opportunity_id"],
            },
            "users": {
                "description": "Usuarios / Owners del CRM",
                "endpoints": {
                    "list": "GET /crm/users",
                },
                "main_fields": ["id", "first_name", "last_name", "email", "status"],
            },
        },
        "headers_required": {
            "Authorization": "Bearer {APIDECK_API_KEY}",
            "x-apideck-app-id": "{APIDECK_APP_ID}",
            "x-apideck-consumer-id": "{consumer_id}",
            "x-apideck-service-id": "hubspot",   # importante
        },
        "notes_for_retell": [
            "Siempre incluir el header x-apideck-service-id: hubspot",
            "Usar el consumer_id del cliente en todas las peticiones",
            "Los campos están unificados por Apideck (no son los nombres originales de HubSpot)",
            "Para crear una actividad de tipo llamada usar type='call'",
        ],
    }

    logger.info(f"[{consumer_id}] Schema CRM solicitado y devuelto")
    return schema


# ======================
# Health check
# ======================
@app.get("/")
async def root():
    return {"status": "ok", "service": "retell-bot-apideck"}
