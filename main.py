import os
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
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
    logger.error("FALTAN variables de entorno APIDECK_API_KEY o APP_ID")
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
# 3. ENDPOINT CLAVE: OBTENER ESTRUCTURA DE LA API Y VOLCAR A LOGS
# ======================
@app.get("/apideck/sync-schema/{consumer_id}")
async def sync_crm_schema_to_logs(consumer_id: str):
    """
    Se ejecuta tras volver de dar permisos en la web. Valida la conexión
    con HubSpot y extrae la estructura completa/metadatos de la API del CRM
    imprimiéndolo de forma detallada en los logs de Render.
    """
    logger.info(f"=== INICIANDO SINCRONIZACIÓN DE SCHEMA PARA CONSUMER: {consumer_id} ===")

    # Verificar estado de conexión
    status = await check_hubspot_connection(consumer_id)
    if not status.get("connected"):
        logger.warning(f"[{consumer_id}] Intento de sincronización sin conexión 'callable' a HubSpot.")
        raise HTTPException(status_code=400, detail="HubSpot no está conectado o listo todavía.")

    headers_hubspot = {
        **apideck_headers(consumer_id),
        "x-apideck-service-id": "hubspot"
    }

    schema_results = {}

    # Recursos principales de CRM a consultar para extraer la estructura/datos
    resources = ["contacts", "companies", "opportunities", "leads"]

    for resource in resources:
        try:
            logger.info(f"[{consumer_id}] Solicitando estructura del recurso CRM: '{resource}' ...")
            res = requests.get(
                f"{APIDECK_BASE}/crm/{resource}",
                headers=headers_hubspot,
                params={"limit": 1},
                timeout=10
            )
            
            if res.status_code < 400:
                schema_results[resource] = res.json()
                logger.info(f"\n--- [HUBSPOT SCHEMA] RECURSO: {resource.upper()} ---")
                logger.info(res.text)
                logger.info(f"--------------------------------------------------\n")
            else:
                logger.warning(f"[{consumer_id}] No se pudo obtener '{resource}': Status {res.status_code} - {res.text}")
        except Exception as err:
            logger.error(f"[{consumer_id}] Error consultando recurso {resource}: {str(err)}")

    logger.info(f"=== FIN DE SINCRONIZACIÓN DE SCHEMA PARA [{consumer_id}] ===")
    
    return {
        "success": True,
        "message": "Estructura de la API del CRM solicitada y volcada con éxito en los logs de Render.",
        "consumer_id": consumer_id,
        "resources_fetched": list(schema_results.keys())
    }


# ======================
# 4. WEBHOOK DE APIDECK
# ======================
@app.api_route("/apideck/webhook", methods=["GET", "POST"])
async def apideck_webhook(request: Request):
    if request.method == "GET":
        challenge = request.query_params.get("challenge", "ok")
        return {"challenge": challenge}

    try:
        body = await request.json()
        if "challenge" in body:
            return {"challenge": body["challenge"]}

        event_type = body.get("event")
        consumer_id = body.get("consumer_id")
        
        logger.info(f"Webhook recibido: evento={event_type} | consumer_id={consumer_id}")

        if event_type in ["vault.connection.added", "vault.connection.callable"] and consumer_id:
            # Opcional: si prefieres automatizarlo por webhook también
            logger.info(f"Conexión establecida por webhook para {consumer_id}")

        return {"status": "received"}
    except Exception as e:
        logger.error(f"Error en webhook: {str(e)}")
        return {"status": "error", "message": str(e)}


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
