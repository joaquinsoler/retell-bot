# ============================================================
# BACKEND COMPLETO - PARTE 1/3
# Retell Bot + Google OAuth + Apideck CRM
# ============================================================

import os
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

# Google OAuth
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from fastapi.responses import HTMLResponse

# ============================================================
# LOGGING REFORZADO (para ver todo claramente en Render)
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("retell-bot")

# ============================================================
# APP + CORS
# ============================================================
app = FastAPI(title="Retell Bot - Google OAuth + Apideck CRM")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# VARIABLES DE ENTORNO
# ============================================================
# Google
GOOGLE_CLIENT_ID = "667952866685-37b9ksse2l8krjo4t7t6tdhdqbk11e34.apps.googleusercontent.com"

# Apideck
APIDECK_API_KEY = os.getenv("APIDECK_API_KEY")
APIDECK_APP_ID = os.getenv("APIDECK_APP_ID")
APIDECK_BASE = "https://unify.apideck.com"

if not APIDECK_API_KEY or not APIDECK_APP_ID:
    logger.error("❌ FALTAN variables de entorno APIDECK_API_KEY o APIDECK_APP_ID")
else:
    logger.info("✅ Variables de entorno Apideck cargadas correctamente")

logger.info(f"✅ Google Client ID configurado: {GOOGLE_CLIENT_ID[:20]}...")

# ============================================================
# MODELOS PYDANTIC
# ============================================================
class TokenRequest(BaseModel):
    token: str

class CreateSessionRequest(BaseModel):
    consumer_id: str
    redirect_uri: Optional[str] = None
    user_name: Optional[str] = None
    account_name: Optional[str] = None

# ============================================================
# HELPERS
# ============================================================
def apideck_headers(consumer_id: str) -> dict:
    """Headers estándar para todas las llamadas a Apideck"""
    return {
        "Authorization": f"Bearer {APIDECK_API_KEY}",
        "x-apideck-app-id": APIDECK_APP_ID,
        "x-apideck-consumer-id": consumer_id,
        "Content-Type": "application/json",
    }
# ============================================================
# BACKEND COMPLETO - PARTE 2/3
# Endpoints: Google OAuth + Crear sesión Vault + Estado de conexión
# ============================================================

# ============================================================
# 1. AUTENTICACIÓN CON GOOGLE
# ============================================================
@app.post("/api/auth/google")
async def google_auth(body: TokenRequest):
    """
    Recibe el token JWT de Google, lo verifica y vuelca
    todos los datos del usuario en los logs de Render.
    """
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
        email_verified = idinfo.get("email_verified", False)

        # Log claro y visible en Render
        logger.info("--------------------------------------------------")
        logger.info("🎉 ¡AUTENTICACIÓN CON GOOGLE EXITOSA!")
        logger.info(f"• Nombre          : {name}")
        logger.info(f"• Correo          : {email}")
        logger.info(f"• Google ID       : {google_id}")
        logger.info(f"• Email verificado: {email_verified}")
        logger.info(f"• Foto de perfil  : {picture}")
        logger.info("--------------------------------------------------")

        return {
            "status": "success",
            "message": "Autenticación recibida e impresa en logs correctamente",
            "email": email,
            "name": name,
            "google_id": google_id,
            "picture": picture
        }

    except ValueError as e:
        logger.error(f"❌ Error de seguridad: Token de Google inválido → {str(e)}")
        raise HTTPException(status_code=401, detail=f"Token de Google inválido: {str(e)}")
    except Exception as e:
        logger.error(f"❌ Error inesperado en autenticación Google: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


# ============================================================
# 2. CREAR SESIÓN DE VAULT (Apideck)
# ============================================================
@app.post("/apideck/session")
async def create_vault_session(body: CreateSessionRequest):
    """
    Crea una sesión de Apideck Vault para que el cliente conecte su CRM (HubSpot).
    """
    logger.info(f"📥 Solicitud de sesión Vault | consumer_id={body.consumer_id}")

    if not APIDECK_API_KEY or not APIDECK_APP_ID:
        logger.error("❌ Apideck no está configurado (faltan API_KEY o APP_ID)")
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
        logger.info(f"🔄 Creando sesión Vault para consumer_id={body.consumer_id} ...")
        response = requests.post(
            f"{APIDECK_BASE}/vault/sessions",
            headers=apideck_headers(body.consumer_id),
            json=payload,
            timeout=15,
        )

        if response.status_code >= 400:
            logger.error(f"❌ Error de Apideck al crear sesión: {response.status_code} | {response.text}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Error de Apideck: {response.text}"
            )

        data = response.json().get("data", {})
        session_uri = data.get("session_uri")
        session_token = data.get("session_token")

        if not session_uri:
            logger.error("❌ Apideck no devolvió session_uri")
            raise HTTPException(status_code=500, detail="No se recibió session_uri de Apideck")

        logger.info(f"✅ Sesión Vault creada correctamente | consumer_id={body.consumer_id}")
        logger.info(f"   session_uri: {session_uri[:80]}...")

        return {
            "success": True,
            "consumer_id": body.consumer_id,
            "session_uri": session_uri,
            "session_token": session_token,
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red al crear sesión Vault: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error de conexión con Apideck: {str(e)}")
    except Exception as e:
        logger.error(f"❌ Error inesperado al crear sesión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 3. COMPROBAR ESTADO DE CONEXIÓN CRM
# ============================================================
@app.get("/apideck/connection-status/{consumer_id}")
async def check_hubspot_connection(consumer_id: str):
    """
    Comprueba si el CRM (HubSpot) está conectado y en estado 'callable'.
    """
    logger.info(f"🔍 Comprobando conexión CRM | consumer_id={consumer_id}")

    if not APIDECK_API_KEY or not APIDECK_APP_ID:
        logger.error("❌ Apideck no configurado")
        raise HTTPException(status_code=500, detail="Apideck no configurado")

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
            logger.error(f"[{consumer_id}] Error al comprobar conexión: {response.status_code} | {response.text}")
            raise HTTPException(status_code=response.status_code, detail=response.text)

        data = response.json().get("data", {})
        state = data.get("state")
        enabled = data.get("enabled", False)
        is_connected = state == "callable" and enabled

        logger.info(f"[{consumer_id}] Estado CRM → connected={is_connected} | state={state} | enabled={enabled}")

        return {
            "connected": is_connected,
            "state": state,
            "enabled": enabled,
            "service_id": data.get("service_id"),
            "name": data.get("name"),
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red al comprobar conexión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Error inesperado al comprobar conexión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
# ============================================================
# BACKEND COMPLETO - PARTE 3/3
# Endpoints: Sync Schema + Webhook + Health Check
# ============================================================

# ============================================================
# 4. SINCRONIZAR SCHEMA DEL CRM → LOGS DE RENDER
# ============================================================
@app.get("/apideck/sync-schema/{consumer_id}")
async def sync_crm_schema_to_logs(consumer_id: str):
    """
    Se ejecuta tras conectar el CRM.
    Valida la conexión y extrae la estructura/metadatos de la API
    (contacts, companies, opportunities, leads) imprimiéndolos
    de forma detallada en los logs de Render.
    """
    logger.info("=" * 60)
    logger.info(f"=== INICIANDO SINCRONIZACIÓN DE SCHEMA | consumer_id={consumer_id} ===")
    logger.info("=" * 60)

    # 1. Verificar que la conexión esté lista
    try:
        status = await check_hubspot_connection(consumer_id)
    except Exception as e:
        logger.error(f"❌ No se pudo verificar el estado de conexión: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error verificando conexión: {str(e)}")

    if not status.get("connected"):
        logger.warning(f"[{consumer_id}] Intento de sincronización sin conexión 'callable' a HubSpot.")
        raise HTTPException(
            status_code=400,
            detail="HubSpot no está conectado o listo todavía."
        )

    headers_hubspot = {
        **apideck_headers(consumer_id),
        "x-apideck-service-id": "hubspot"
    }

    schema_results = {}
    resources = ["contacts", "companies", "opportunities", "leads"]

    for resource in resources:
        try:
            logger.info(f"[{consumer_id}] Solicitando estructura del recurso CRM: '{resource}' ...")
            res = requests.get(
                f"{APIDECK_BASE}/crm/{resource}",
                headers=headers_hubspot,
                params={"limit": 1},
                timeout=12
            )

            if res.status_code < 400:
                schema_results[resource] = res.json()
                logger.info(f"\n--- [HUBSPOT SCHEMA] RECURSO: {resource.upper()} ---")
                logger.info(res.text)
                logger.info("-" * 50)
            else:
                logger.warning(
                    f"[{consumer_id}] No se pudo obtener '{resource}': "
                    f"Status {res.status_code} - {res.text}"
                )
        except requests.exceptions.RequestException as err:
            logger.error(f"[{consumer_id}] Error de red consultando recurso {resource}: {str(err)}")
        except Exception as err:
            logger.error(f"[{consumer_id}] Error inesperado consultando recurso {resource}: {str(err)}")

    logger.info("=" * 60)
    logger.info(f"=== FIN DE SINCRONIZACIÓN DE SCHEMA | consumer_id={consumer_id} ===")
    logger.info(f"Recursos obtenidos: {list(schema_results.keys())}")
    logger.info("=" * 60)

    return {
        "success": True,
        "message": "Estructura de la API del CRM solicitada y volcada con éxito en los logs de Render.",
        "consumer_id": consumer_id,
        "resources_fetched": list(schema_results.keys()),
        "crm_name": status.get("name", "HubSpot")
    }


# ============================================================
# 5. WEBHOOK DE APIDECK
# ============================================================
@app.api_route("/apideck/webhook", methods=["GET", "POST"])
async def apideck_webhook(request: Request):
    """
    Recibe eventos de Apideck (conexión añadida, callable, etc.).
    """
    if request.method == "GET":
        challenge = request.query_params.get("challenge", "ok")
        logger.info(f"Webhook GET challenge recibido: {challenge}")
        return {"challenge": challenge}

    try:
        body = await request.json()

        # Respuesta al challenge de verificación
        if "challenge" in body:
            logger.info(f"Webhook challenge (POST) recibido: {body['challenge']}")
            return {"challenge": body["challenge"]}

        event_type = body.get("event")
        consumer_id = body.get("consumer_id")

        logger.info(f"📩 Webhook recibido → evento={event_type} | consumer_id={consumer_id}")

        if event_type in ["vault.connection.added", "vault.connection.callable"] and consumer_id:
            logger.info(f"✅ Conexión establecida por webhook para consumer_id={consumer_id}")
            # Aquí podrías disparar automáticamente el sync-schema si lo deseas

        return {"status": "received"}

    except Exception as e:
        logger.error(f"❌ Error procesando webhook: {str(e)}")
        return {"status": "error", "message": str(e)}


# ============================================================
# 6. HEALTH CHECK
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def root():
    # Sirve el frontend completo
    # (pega aquí todo el HTML que te he mandado en las 3 partes)
    html_content = """
    <!DOCTYPE html>
    ... aquí va TODO el HTML completo ...
    </html>
    """
    return HTMLResponse(content=html_content)
    }
# ============================================================
# 7. ELIMINAR CONEXIÓN CRM (para poder empezar de cero)
# ============================================================
@app.delete("/apideck/disconnect/{consumer_id}")
async def disconnect_crm(consumer_id: str):
    """
    Elimina la conexión de HubSpot del consumer para poder repetir el flujo.
    """
    logger.info(f"🗑️ Solicitando eliminación de conexión CRM | consumer_id={consumer_id}")

    if not APIDECK_API_KEY or not APIDECK_APP_ID:
        logger.error("❌ Apideck no configurado")
        raise HTTPException(status_code=500, detail="Apideck no configurado")

    try:
        response = requests.delete(
            f"{APIDECK_BASE}/vault/connections/crm/hubspot",
            headers=apideck_headers(consumer_id),
            timeout=10,
        )

        # 204 = eliminado correctamente | 404 = ya no existía
        if response.status_code in (204, 404):
            logger.info(f"✅ Conexión CRM eliminada (o no existía) | consumer_id={consumer_id}")
            return {
                "success": True,
                "message": "Conexión CRM eliminada correctamente",
                "consumer_id": consumer_id
            }

        logger.error(f"❌ Error al eliminar conexión: {response.status_code} | {response.text}")
        raise HTTPException(status_code=response.status_code, detail=response.text)

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red al eliminar conexión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Error inesperado al eliminar conexión: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# FIN DEL BACKEND
# ============================================================
