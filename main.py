# ============================================================
# BACKEND COMPLETO - PARTE 1/3
# Dansu - Google OAuth + Apideck CRM
# ============================================================

import os
import logging
import io
import csv
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests

# Google OAuth
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

# ============================================================
# LOGGING REFORZADO
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("retell-bot")

# ============================================================
# APP + CORS
# ============================================================
app = FastAPI(title="Dansu - Google OAuth + Apideck CRM")

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
GOOGLE_CLIENT_ID = "667952866685-37b9ksse2l8krjo4t7t6tdhdqbk11e34.apps.googleusercontent.com"

APIDECK_API_KEY = os.getenv("APIDECK_API_KEY")
APIDECK_APP_ID = os.getenv("APIDECK_APP_ID")
APIDECK_BASE = "https://unify.apideck.com"

if not APIDECK_API_KEY or not APIDECK_APP_ID:
    logger.error("❌ FALTAN variables de entorno APIDECK_API_KEY o APIDECK_APP_ID")
else:
    logger.info("✅ Variables de entorno Apideck cargadas correctamente")

logger.info(f"✅ Google Client ID configurado: {GOOGLE_CLIENT_ID[:20]}...")

# ============================================================
# ALMACÉN TEMPORAL EN MEMORIA (solo para logs)
# ============================================================
google_sessions = {}   # consumer_id → {name, email, google_id, picture}

# ============================================================
# MODELOS PYDANTIC
# ============================================================
class TokenRequest(BaseModel):
    token: str

class AssociateRequest(BaseModel):
    consumer_id: str
    email: str
    name: Optional[str] = None
    google_id: Optional[str] = None
    picture: Optional[str] = None

class CreateSessionRequest(BaseModel):
    consumer_id: str
    redirect_uri: Optional[str] = None
    user_name: Optional[str] = None
    account_name: Optional[str] = None

# ============================================================
# HELPERS
# ============================================================
def apideck_headers(consumer_id: str) -> dict:
    return {
        "Authorization": f"Bearer {APIDECK_API_KEY}",
        "x-apideck-app-id": APIDECK_APP_ID,
        "x-apideck-consumer-id": consumer_id,
        "Content-Type": "application/json",
    }
# ============================================================
# BACKEND COMPLETO - PARTE 2/3
# Endpoints de Google + Apideck
# ============================================================

# ============================================================
# 1. AUTENTICACIÓN CON GOOGLE
# ============================================================
@app.post("/api/auth/google")
async def google_auth(body: TokenRequest):
    token = body.token

    if not token:
        logger.error("❌ Error: No se ha proporcionado ningún token de Google")
        raise HTTPException(status_code=400, detail="No token provided")

    try:
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
# 1b. ASOCIAR DATOS DE GOOGLE AL CONSUMER_ID (para los logs)
# ============================================================
@app.post("/api/auth/google/associate")
async def associate_google(body: AssociateRequest):
    google_sessions[body.consumer_id] = {
        "name": body.name,
        "email": body.email,
        "google_id": body.google_id,
        "picture": body.picture
    }
    logger.info(f"🔗 Asociado Google → consumer_id={body.consumer_id} | email={body.email}")
    return {"status": "ok"}


# ============================================================
# 2. CREAR SESIÓN DE VAULT (Apideck)
# ============================================================
@app.post("/apideck/session")
async def create_vault_session(body: CreateSessionRequest):
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
# 4. HABILITAR CONEXIÓN (cuando está en 'available')
# ============================================================
@app.post("/apideck/enable/{consumer_id}")
async def enable_crm_connection(consumer_id: str):
    logger.info(f"⚡ Intentando habilitar conexión CRM | consumer_id={consumer_id}")

    if not APIDECK_API_KEY or not APIDECK_APP_ID:
        raise HTTPException(status_code=500, detail="Apideck no configurado")

    try:
        response = requests.patch(
            f"{APIDECK_BASE}/vault/connections/crm/hubspot",
            headers=apideck_headers(consumer_id),
            json={"enabled": True},
            timeout=10,
        )

        logger.info(f"Respuesta enable → {response.status_code} | {response.text[:300]}")

        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)

        data = response.json().get("data", {})
        return {
            "success": True,
            "state": data.get("state"),
            "enabled": data.get("enabled"),
            "message": "Intento de habilitación realizado"
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red al habilitar: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 5. SINCRONIZAR SCHEMA + IMPRESIÓN ESTILO REGISTRO + DATOS GOOGLE
# ============================================================
@app.get("/apideck/sync-schema/{consumer_id}")
async def sync_crm_schema_to_logs(consumer_id: str):
    logger.info(f"=== INICIANDO SINCRONIZACIÓN DE SCHEMA PARA: {consumer_id} ===")

    try:
        status = await check_hubspot_connection(consumer_id)
    except Exception as e:
        logger.error(f"❌ No se pudo verificar el estado de conexión: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error verificando conexión: {str(e)}")

    if not status.get("connected"):
        logger.warning(f"[{consumer_id}] Intento de sincronización sin conexión 'callable'.")
        raise HTTPException(status_code=400, detail="HubSpot no está conectado o listo todavía.")

    headers_hubspot = {
        **apideck_headers(consumer_id),
        "x-apideck-service-id": "hubspot"
    }

    resources = ["contacts", "companies", "opportunities", "leads"]
    schema_results = {}

    for resource in resources:
        try:
            res = requests.get(
                f"{APIDECK_BASE}/crm/{resource}",
                headers=headers_hubspot,
                params={"limit": 1},
                timeout=12
            )
            if res.status_code < 400:
                schema_results[resource] = res.json()
            else:
                schema_results[resource] = {"status": res.status_code, "error": res.text}
        except Exception as err:
            schema_results[resource] = {"error": str(err)}

    # Identificador de asistente (igual que REGISTRO)
    assistant_id = f"ast-{os.urandom(4).hex()}"

    # Salida tipo CSV (exactamente igual que el código REGISTRO)
    csv_buffer = io.StringIO()
    csv_writer = csv.writer(csv_buffer, delimiter=';')
    csv_writer.writerow(["email / consumer_id", "assistant_id", "crm_provider", "recurso", "estado"])

    for resource in resources:
        status_text = "Disponible / Estructurado OK" if "error" not in str(schema_results.get(resource, {})) else "Error de esquema"
        csv_writer.writerow([consumer_id, assistant_id, "hubspot", resource, status_text])

    csv_output = csv_buffer.getvalue().strip()

    # Datos de Google asociados
    google_info = google_sessions.get(consumer_id, {})
    google_name  = google_info.get("name", "No disponible en esta sesión")
    google_email = google_info.get("email", consumer_id)
    google_id    = google_info.get("google_id", "-")

    # LOG FINAL (estilo REGISTRO + datos de Google)
    logger.info(
        f"\n"
        f"==================================================\n"
        f"✅ ÉXITO: NUEVO ASISTENTE CREADO / SCHEMA EXTRAÍDO\n"
        f"--------------------------------------------------\n"
        f"DATOS DE GOOGLE:\n"
        f"  • Nombre     : {google_name}\n"
        f"  • Email      : {google_email}\n"
        f"  • Google ID  : {google_id}\n"
        f"--------------------------------------------------\n"
        f"DATOS DEL CRM (HubSpot):\n"
        f"{csv_output}\n"
        f"=================================================="
    )

    # Schema completo de cada recurso (para depuración)
    for resource, data in schema_results.items():
        logger.info(f"\n--- [HUBSPOT SCHEMA] RECURSO: {resource.upper()} ---")
        logger.info(str(data)[:2000])
        logger.info("-" * 50)

    return {
        "success": True,
        "message": "Nuevo asistente / schema extraído con éxito y volcado a los logs.",
        "assistant_id": assistant_id,
        "consumer_id": consumer_id,
        "resources_fetched": list(schema_results.keys())
    }


# ============================================================
# 6. ELIMINAR CONEXIÓN CRM
# ============================================================
@app.delete("/apideck/disconnect/{consumer_id}")
async def disconnect_crm(consumer_id: str):
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

        if response.status_code in (204, 404):
            logger.info(f"✅ Conexión CRM eliminada (o no existía) | consumer_id={consumer_id}")
            # Limpiamos también la sesión de Google en memoria
            google_sessions.pop(consumer_id, None)
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
# 7. WEBHOOK DE APIDECK
# ============================================================
@app.api_route("/apideck/webhook", methods=["GET", "POST"])
async def apideck_webhook(request: Request):
    if request.method == "GET":
        challenge = request.query_params.get("challenge", "ok")
        logger.info(f"Webhook GET challenge recibido: {challenge}")
        return {"challenge": challenge}

    try:
        body = await request.json()

        if "challenge" in body:
            logger.info(f"Webhook challenge (POST) recibido: {body['challenge']}")
            return {"challenge": body["challenge"]}

        event_type = body.get("event")
        consumer_id = body.get("consumer_id")

        logger.info(f"📩 Webhook recibido → evento={event_type} | consumer_id={consumer_id}")

        if event_type in ["vault.connection.added", "vault.connection.callable"] and consumer_id:
            logger.info(f"✅ Conexión establecida por webhook para consumer_id={consumer_id}")

        return {"status": "received"}

    except Exception as e:
        logger.error(f"❌ Error procesando webhook: {str(e)}")
        return {"status": "error", "message": str(e)}
# ============================================================
# BACKEND COMPLETO - PARTE 3/3
# Endpoint raíz que sirve el frontend completo
# ============================================================

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "retell-bot-google-apideck",
        "google_configured": bool(GOOGLE_CLIENT_ID),
        "apideck_configured": bool(APIDECK_API_KEY and APIDECK_APP_ID)
    }
    


# ============================================================
# FIN DEL BACKEND
# ============================================================
