# ======================
# PARTE 1/3 - Configuración base + Google Auth
# ======================

import os
import logging
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
import requests

# ======================
# LOGGING
# ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("dansu-saas")

app = FastAPI(title="Dansu SaaS - CRM Connection")

# ======================
# CORS
# ======================
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
GOOGLE_CLIENT_ID = os.getenv(
    "GOOGLE_CLIENT_ID",
    "667952866685-37b9ksse2l8krjo4t7t6tdhdqbk11e34.apps.googleusercontent.com"
)

APIDECK_API_KEY = os.getenv("APIDECK_API_KEY")
APIDECK_APP_ID = os.getenv("APIDECK_APP_ID")
APIDECK_BASE = "https://unify.apideck.com"

# ======================
# MODELOS
# ======================
class TokenRequest(BaseModel):
    token: str

class ApideckSessionRequest(BaseModel):
    consumer_id: str          # usaremos el email del usuario
    user_name: Optional[str] = None
    account_name: Optional[str] = None

# ======================
# HELPERS APIDECK
# ======================
def apideck_headers(consumer_id: str) -> dict:
    if not APIDECK_API_KEY or not APIDECK_APP_ID:
        raise HTTPException(
            status_code=500,
            detail="Faltan APIDECK_API_KEY o APIDECK_APP_ID en las variables de entorno"
        )
    return {
        "Authorization": f"Bearer {APIDECK_API_KEY}",
        "x-apideck-app-id": APIDECK_APP_ID,
        "x-apideck-consumer-id": consumer_id,
        "Content-Type": "application/json",
    }

# ======================
# 1. GOOGLE AUTH
# ======================
@app.post("/api/auth/google")
def google_auth(body: TokenRequest):
    token = body.token

    if not token:
        logger.error("❌ Error: No se ha proporcionado ningún token de Google")
        raise HTTPException(status_code=400, detail="No token provided")

    try:
        # Verificar el token con los servidores oficiales de Google
        idinfo = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )

        google_id = idinfo.get("sub")
        email = idinfo.get("email")
        name = idinfo.get("name", "Sin nombre")
        picture = idinfo.get("picture", "")

        if not email:
            logger.error("❌ Error: El token de Google no contiene email")
            raise HTTPException(status_code=400, detail="Token sin email")

        # Logs claros y bonitos
        logger.info("==================================================")
        logger.info("🎉 ¡AUTENTICACIÓN CON GOOGLE EXITOSA!")
        logger.info(f"• Nombre      : {name}")
        logger.info(f"• Email       : {email}")
        logger.info(f"• Google ID   : {google_id}")
        logger.info(f"• Foto        : {picture}")
        logger.info("==================================================")

        return {
            "status": "success",
            "message": "Autenticación recibida correctamente",
            "email": email,
            "name": name,
            "picture": picture,
            "google_id": google_id
        }

    except ValueError as e:
        logger.error(f"❌ Token de Google inválido: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Token de Google inválido: {str(e)}")
    except Exception as e:
        logger.error(f"❌ Error inesperado en Google Auth: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")
# ======================
# PARTE 2/3 - Apideck: Sesión Vault + Estado de conexión
# ======================

@app.post("/apideck/session")
async def create_vault_session(body: ApideckSessionRequest):
    """
    Crea una sesión de Apideck Vault para que el usuario conecte su CRM.
    consumer_id = email del usuario (lo usamos como identificador único).
    """
    consumer_id = body.consumer_id

    if not consumer_id:
        logger.error("❌ Error: consumer_id (email) no proporcionado")
        raise HTTPException(status_code=400, detail="consumer_id es obligatorio")

    redirect_uri = "https://www.dansu.info"  # vuelve a la misma página

    payload = {
        "redirect_uri": redirect_uri,
        "consumer_metadata": {
            "account_name": body.account_name or "Cliente Dansu",
            "user_name": body.user_name or consumer_id,
        },
        "settings": {
            "unified_apis": ["crm"],
            "auto_redirect": True,
            "isolation_mode": True,
            "hide_guides": True,
        },
    }

    try:
        logger.info(f"🔄 Creando sesión de Apideck Vault para: {consumer_id}")

        response = requests.post(
            f"{APIDECK_BASE}/vault/sessions",
            headers=apideck_headers(consumer_id),
            json=payload,
            timeout=15,
        )

        if response.status_code >= 400:
            error_detail = response.text
            logger.error(f"❌ Error Apideck al crear sesión ({response.status_code}): {error_detail}")
            raise HTTPException(status_code=response.status_code, detail=error_detail)

        data = response.json().get("data", {})
        session_uri = data.get("session_uri")
        session_token = data.get("session_token")

        if not session_uri:
            logger.error("❌ Apideck no devolvió session_uri")
            raise HTTPException(status_code=500, detail="No se recibió session_uri de Apideck")

        logger.info(f"✅ Sesión de Vault creada correctamente para {consumer_id}")
        logger.info(f"   → session_uri: {session_uri}")

        return {
            "success": True,
            "session_uri": session_uri,
            "session_token": session_token,
            "consumer_id": consumer_id
        }

    except HTTPException:
        raise
    except requests.exceptions.Timeout:
        logger.error(f"❌ Timeout al contactar con Apideck para {consumer_id}")
        raise HTTPException(status_code=504, detail="Timeout al contactar con Apideck")
    except Exception as e:
        logger.error(f"❌ Error inesperado creando sesión Apideck: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


@app.get("/apideck/connection-status/{consumer_id}")
async def check_crm_connection(consumer_id: str):
    """
    Comprueba si el usuario ya tiene un CRM conectado en Apideck.
    Por ahora miramos principalmente HubSpot (puedes ampliar después).
    """
    if not consumer_id:
        raise HTTPException(status_code=400, detail="consumer_id es obligatorio")

    try:
        logger.info(f"🔍 Comprobando estado de conexión CRM para: {consumer_id}")

        response = requests.get(
            f"{APIDECK_BASE}/vault/connections/crm/hubspot",
            headers=apideck_headers(consumer_id),
            timeout=10,
        )

        if response.status_code == 404:
            logger.info(f"ℹ️ No hay conexión HubSpot para {consumer_id}")
            return {
                "connected": False,
                "provider": None,
                "state": "not_found"
            }

        if response.status_code >= 400:
            logger.error(f"❌ Error al consultar conexión ({response.status_code}): {response.text}")
            raise HTTPException(status_code=response.status_code, detail=response.text)

        data = response.json().get("data", {})
        state = data.get("state")
        enabled = data.get("enabled", False)
        connected = (state == "callable" and enabled)

        provider = "hubspot" if connected else None

        logger.info(f"{'✅' if connected else 'ℹ️'} Estado CRM para {consumer_id}: connected={connected}, state={state}")

        return {
            "connected": connected,
            "provider": provider,
            "state": state,
            "enabled": enabled
        }

    except HTTPException:
        raise
    except requests.exceptions.Timeout:
        logger.error(f"❌ Timeout al consultar estado de conexión para {consumer_id}")
        raise HTTPException(status_code=504, detail="Timeout al contactar con Apideck")
    except Exception as e:
        logger.error(f"❌ Error inesperado comprobando conexión: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")
# ======================
# PARTE 3/3 - Extracción de esquema CRM + Root
# ======================

@app.get("/apideck/sync-schema/{consumer_id}")
async def sync_crm_schema(consumer_id: str):
    """
    Extrae la estructura (schema) del CRM del cliente y la imprime en los logs.
    NO guarda nada en base de datos.
    """
    if not consumer_id:
        logger.error("❌ Error: consumer_id no proporcionado")
        raise HTTPException(status_code=400, detail="consumer_id es obligatorio")

    logger.info("==================================================")
    logger.info(f"🔄 INICIANDO EXTRACCIÓN DE ESQUEMA CRM")
    logger.info(f"• Usuario (consumer_id): {consumer_id}")
    logger.info("==================================================")

    headers = {**apideck_headers(consumer_id), "x-apideck-service-id": "hubspot"}
    resources = ["contacts", "companies", "opportunities", "leads"]
    schema_results: Dict[str, Any] = {}
    provider = "hubspot"

    for resource in resources:
        try:
            logger.info(f"   → Extrayendo recurso: {resource}...")
            res = requests.get(
                f"{APIDECK_BASE}/crm/{resource}",
                headers=headers,
                params={"limit": 1},
                timeout=12
            )

            if res.status_code < 400:
                data = res.json()
                schema_results[resource] = {
                    "status": "ok",
                    "sample": data
                }
                logger.info(f"     ✅ {resource}: OK")
            else:
                schema_results[resource] = {
                    "status": "error",
                    "code": res.status_code,
                    "error": res.text
                }
                logger.warning(f"     ⚠️ {resource}: Error {res.status_code}")

        except requests.exceptions.Timeout:
            schema_results[resource] = {"status": "timeout", "error": "Timeout"}
            logger.error(f"     ❌ {resource}: Timeout")
        except Exception as err:
            schema_results[resource] = {"status": "error", "error": str(err)}
            logger.error(f"     ❌ {resource}: {str(err)}")

    # ======================
    # RESUMEN FINAL EN LOGS
    # ======================
    logger.info("==================================================")
    logger.info("✅ EXTRACCIÓN DE CRM FINALIZADA")
    logger.info(f"• Usuario        : {consumer_id}")
    logger.info(f"• CRM Provider   : {provider}")
    logger.info("• Recursos:")

    for resource, result in schema_results.items():
        status = result.get("status", "unknown")
        if status == "ok":
            logger.info(f"   - {resource}: Disponible / Estructura OK")
        else:
            logger.info(f"   - {resource}: {status.upper()} → {result.get('error', '')[:120]}")

    logger.info("==================================================")
    logger.info("📄 DATOS COMPLETOS DEL ESQUEMA (JSON):")
    logger.info(str(schema_results))
    logger.info("==================================================")

    return {
        "success": True,
        "message": "Esquema del CRM extraído e impreso en logs correctamente",
        "consumer_id": consumer_id,
        "provider": provider,
        "resources": list(schema_results.keys()),
        "schema_summary": {
            k: v.get("status") for k, v in schema_results.items()
        }
    }


@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "Dansu SaaS - Google Auth + Apideck CRM Extraction",
        "endpoints": [
            "POST /api/auth/google",
            "POST /apideck/session",
            "GET  /apideck/connection-status/{consumer_id}",
            "GET  /apideck/sync-schema/{consumer_id}"
        ]
    }
