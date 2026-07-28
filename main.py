# ============================================================
# PARTE 1/3 - Imports, configuración, CORS y autenticación Google
# ============================================================

import os
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

# ======================
# LOGGING
# ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("dansu")

app = FastAPI(title="Dansu - Asistentes Telefónicos + CRM")

# ======================
# CORS (permite dansu.info y desarrollo)
# ======================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://dansu.info",
        "https://www.dansu.info",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "*"  # temporal mientras terminamos de probar
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# GOOGLE OAUTH
# ======================
GOOGLE_CLIENT_ID = "667952866685-37b9ksse2l8krjo4t7t6tdhdqbk11e34.apps.googleusercontent.com"

class TokenRequest(BaseModel):
    token: str

@app.post("/api/auth/google")
def google_auth(body: TokenRequest):
    token = body.token

    if not token:
        logger.error("❌ Error: No se ha proporcionado ningún token.")
        raise HTTPException(status_code=400, detail="No token provided")

    try:
        # Verificar el token con los servidores oficiales de Google
        idinfo = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )

        # Extraer los datos del usuario
        google_id = idinfo["sub"]
        email = idinfo["email"]
        name = idinfo.get("name", "Sin nombre")
        picture = idinfo.get("picture", "")

        # Imprimir en los logs de Render (exactamente como pediste)
        print("--------------------------------------------------")
        print("🎉 ¡AUTENTICACIÓN CON GOOGLE EXITOSA! 🎉")
        print(f"• Nombre: {name}")
        print(f"• Correo electrónico: {email}")
        print(f"• Google ID: {google_id}")
        print(f"• Foto de perfil: {picture}")
        print("--------------------------------------------------")

        return {
            "status": "success",
            "message": "Autenticación recibida e impresa en logs correctamente",
            "google_id": google_id,
            "email": email,
            "name": name,
            "picture": picture
        }

    except ValueError as e:
        logger.error(f"❌ Error de seguridad: Token de Google inválido -> {str(e)}")
        raise HTTPException(status_code=401, detail=f"Token de Google inválido: {str(e)}")
# ============================================================
# PARTE 2/3 - Variables de Apideck, modelos, helpers y endpoints
#            de sesión + estado de conexión
# ============================================================

# ======================
# VARIABLES DE ENTORNO APIDECK
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
    Crea una sesión de Apideck Vault para que el cliente conecte su CRM.
    La redirect_uri por defecto es https://dansu.info
    """
    logger.info(f"Solicitud de sesión recibida | consumer_id={body.consumer_id}")

    if not APIDECK_API_KEY or not APIDECK_APP_ID:
        logger.error("Apideck no está configurado (faltan API_KEY o APP_ID)")
        raise HTTPException(status_code=500, detail="Apideck no configurado en el servidor")

    # Importante: por defecto volvemos a la página de Wix
    redirect_uri = body.redirect_uri or "https://dansu.info"

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
    Comprueba si el CRM (HubSpot) está conectado y en estado 'callable'.
    """
    logger.info(f"Comprobando conexión CRM | consumer_id={consumer_id}")

    try:
        response = requests.get(
            f"{APIDECK_BASE}/vault/connections/crm/hubspot",
            headers=apideck_headers(consumer_id),
            timeout=10,
        )

        if response.status_code == 404:
            logger.info(f"[{consumer_id}] CRM NO está conectado (404)")
            return {
                "connected": False,
                "state": None,
                "name": None,
                "message": "CRM no conectado"
            }

        if response.status_code >= 400:
            logger.error(f"[{consumer_id}] Error al comprobar conexión: {response.text}")
            raise HTTPException(status_code=response.status_code, detail=response.text)

        data = response.json().get("data", {})
        state = data.get("state")
        enabled = data.get("enabled", False)
        is_connected = state == "callable" and enabled
        crm_name = data.get("name") or "HubSpot"

        return {
            "connected": is_connected,
            "state": state,
            "enabled": enabled,
            "service_id": data.get("service_id"),
            "name": crm_name,
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Error de red al comprobar conexión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
# ============================================================
# PARTE 3/3 - Sync schema (imprime estructura del CRM en logs),
#            webhook y health check
# ============================================================

# ======================
# 3. SINCRONIZAR SCHEMA DEL CRM → LOGS DE RENDER
# ======================
@app.get("/apideck/sync-schema/{consumer_id}")
async def sync_crm_schema_to_logs(consumer_id: str):
    """
    Se ejecuta tras volver de dar permisos.
    Valida la conexión y extrae la estructura completa del CRM
    imprimiéndola de forma detallada en los logs de Render.
    """
    logger.info(f"=== INICIANDO SINCRONIZACIÓN DE SCHEMA PARA CONSUMER: {consumer_id} ===")

    # Verificar estado de conexión
    status = await check_hubspot_connection(consumer_id)
    if not status.get("connected"):
        logger.warning(f"[{consumer_id}] Intento de sincronización sin conexión 'callable'.")
        raise HTTPException(status_code=400, detail="El CRM no está conectado o listo todavía.")

    headers_crm = {
        **apideck_headers(consumer_id),
        "x-apideck-service-id": "hubspot"
    }

    schema_results = {}

    # Recursos principales de CRM
    resources = ["contacts", "companies", "opportunities", "leads"]

    for resource in resources:
        try:
            logger.info(f"[{consumer_id}] Solicitando estructura del recurso CRM: '{resource}' ...")
            res = requests.get(
                f"{APIDECK_BASE}/crm/{resource}",
                headers=headers_crm,
                params={"limit": 1},
                timeout=10
            )

            if res.status_code < 400:
                schema_results[resource] = res.json()
                logger.info(f"\n--- [CRM SCHEMA] RECURSO: {resource.upper()} ---")
                logger.info(res.text)
                logger.info(f"--------------------------------------------------\n")
            else:
                logger.warning(
                    f"[{consumer_id}] No se pudo obtener '{resource}': "
                    f"Status {res.status_code} - {res.text}"
                )
        except Exception as err:
            logger.error(f"[{consumer_id}] Error consultando recurso {resource}: {str(err)}")

    logger.info(f"=== FIN DE SINCRONIZACIÓN DE SCHEMA PARA [{consumer_id}] ===")

    return {
        "success": True,
        "message": "Estructura de la API del CRM solicitada y volcada con éxito en los logs de Render.",
        "consumer_id": consumer_id,
        "crm_name": status.get("name", "HubSpot"),
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
        "service": "dansu-retell-bot",
        "google_configured": bool(GOOGLE_CLIENT_ID),
        "apideck_configured": bool(APIDECK_API_KEY and APIDECK_APP_ID)
    }
