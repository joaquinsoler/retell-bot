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

@app.get("/", response_class=HTMLResponse)
async def root():
    """
    Sirve el frontend completo.
    Cuando Apideck redirige a https://retell-bot.onrender.com
    el usuario vuelve directamente a la interfaz de Dansu.
    """
    html_content = """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dansu Technologies - Asistentes Virtuales</title>
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
      background: linear-gradient(145deg, #0f172a 0%, #1e3a5f 50%, #0f172a 100%);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      color: #1e293b;
    }
    .card {
      background: #ffffff;
      border-radius: 20px;
      padding: 48px 40px;
      max-width: 460px;
      width: 100%;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.4);
      text-align: center;
    }
    .logo {
      font-size: 26px;
      font-weight: 700;
      color: #0f172a;
      letter-spacing: -0.5px;
      margin-bottom: 6px;
    }
    .subtitle {
      color: #64748b;
      font-size: 15px;
      margin-bottom: 36px;
      line-height: 1.5;
    }
    #googleButtonContainer {
      display: flex;
      justify-content: center;
      margin-bottom: 20px;
      min-height: 44px;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      width: 100%;
      padding: 14px 24px;
      border: none;
      border-radius: 12px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      margin-top: 12px;
    }
    .btn:disabled {
      opacity: 0.55;
      cursor: not-allowed;
      transform: none !important;
    }
    .btn-crm {
      background: #1e40af;
      color: white;
      display: none;
    }
    .btn-crm:hover:not(:disabled) {
      background: #1e3a8a;
      transform: translateY(-1px);
    }
    .btn-agent {
      background: #0f172a;
      color: white;
      display: none;
    }
    .btn-agent:hover:not(:disabled) {
      background: #1e293b;
      transform: translateY(-1px);
    }
    .btn-reset {
      display: none;
      margin-top: 28px;
      background: transparent;
      color: #94a3b8;
      border: 1px solid #e2e8f0;
      font-size: 13px;
      font-weight: 500;
      padding: 10px 18px;
      border-radius: 10px;
      cursor: pointer;
      transition: all 0.2s ease;
      width: auto;
    }
    .btn-reset:hover {
      background: #f8fafc;
      color: #475569;
      border-color: #cbd5e1;
    }
    .status {
      margin-top: 24px;
      padding: 14px 16px;
      border-radius: 12px;
      font-size: 14px;
      display: none;
      word-break: break-word;
      line-height: 1.45;
    }
    .status.loading { display: block; background: #eff6ff; color: #1e40af; }
    .status.success { display: block; background: #f0fdf4; color: #166534; }
    .status.error   { display: block; background: #fef2f2; color: #b91c1c; }
    .status.info    { display: block; background: #f8fafc; color: #334155; }
    .consumer-info {
      margin-top: 28px;
      font-size: 12px;
      color: #94a3b8;
    }
    .crm-badge {
      display: none;
      margin-top: 20px;
      padding: 10px 16px;
      background: #ecfdf5;
      border: 1px solid #a7f3d0;
      border-radius: 10px;
      color: #065f46;
      font-size: 14px;
      font-weight: 600;
    }
    .spinner {
      width: 18px;
      height: 18px;
      border: 2px solid rgba(255,255,255,0.3);
      border-top-color: white;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .divider {
      height: 1px;
      background: #e2e8f0;
      margin: 24px 0;
      display: none;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">Dansu Technologies</div>
    <p class="subtitle">Asistentes virtuales telefónicos conectados a tu CRM</p>

    <div id="googleButtonContainer"></div>
    <div id="divider" class="divider"></div>

    <button id="connectCrmBtn" class="btn btn-crm" onclick="connectCRM()">
      Conectar con CRM
    </button>

    <div id="crmBadge" class="crm-badge"></div>

    <button id="createAgentBtn" class="btn btn-agent" onclick="createAgent()">
      Crear agente telefónico
    </button>

    <div id="status" class="status"></div>

    <button id="resetBtn" class="btn btn-reset" onclick="resetAll()">
      Volver a empezar
    </button>

    <div class="consumer-info">
      ID de cliente: <span id="consumerIdDisplay">-</span>
    </div>
  </div>

  <script>
    const BACKEND_URL = "https://retell-bot.onrender.com";
    const GOOGLE_CLIENT_ID = "667952866685-37b9ksse2l8krjo4t7t6tdhdqbk11e34.apps.googleusercontent.com";

    let consumerId = localStorage.getItem("dansu_consumer_id");
    if (!consumerId) {
      consumerId = "user-" + Date.now();
      localStorage.setItem("dansu_consumer_id", consumerId);
    }
    document.getElementById("consumerIdDisplay").textContent = consumerId;

    let isGoogleLoggedIn = false;
    let googleUser = null;

    function showStatus(message, type = "loading") {
      const status = document.getElementById("status");
      status.className = `status ${type}`;
      status.innerHTML = message;
    }

    function hideStatus() {
      const status = document.getElementById("status");
      status.className = "status";
      status.innerHTML = "";
    }

    function handleCredentialResponse(response) {
      const idToken = response.credential;
      showStatus("Verificando inicio de sesión con Google...", "loading");

      fetch(`${BACKEND_URL}/api/auth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: idToken })
      })
      .then(res => res.json())
      .then(data => {
        if (data.status === "success") {
          isGoogleLoggedIn = true;
          googleUser = {
            email: data.email,
            name: data.name,
            picture: data.picture,
            google_id: data.google_id
          };
          console.log("Google login OK:", googleUser);
          showStatus(`✅ Sesión iniciada como <strong>${data.name || data.email}</strong>`, "success");
          document.getElementById("connectCrmBtn").style.display = "inline-flex";
          document.getElementById("divider").style.display = "block";
          document.getElementById("resetBtn").style.display = "inline-flex";

          // Asociar datos de Google al consumer_id para los logs posteriores
          fetch(`${BACKEND_URL}/api/auth/google/associate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              consumer_id: consumerId,
              email: data.email,
              name: data.name,
              google_id: data.google_id,
              picture: data.picture
            })
          }).catch(() => {});
        } else {
          throw new Error(data.detail || data.message || "Error en autenticación");
        }
      })
      .catch(err => {
        console.error("Error:", err);
        showStatus("Error al procesar el inicio de sesión: " + err.message, "error");
      });
    }

    function initializeGoogleButton() {
      if (typeof google === "undefined" || !google.accounts) {
        setTimeout(initializeGoogleButton, 200);
        return;
      }
      google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: handleCredentialResponse,
        auto_select: false,
        cancel_on_tap_outside: true
      });
      google.accounts.id.renderButton(
        document.getElementById("googleButtonContainer"),
        {
          type: "standard",
          shape: "rectangular",
          theme: "outline",
          text: "signin_with",
          size: "large",
          logo_alignment: "left",
          width: 320
        }
      );
    }

    async function connectCRM() {
      const btn = document.getElementById("connectCrmBtn");
      btn.disabled = true;
      btn.innerHTML = `<div class="spinner"></div> Creando sesión...`;
      showStatus("Preparando conexión con tu CRM...", "loading");

      try {
        const response = await fetch(`${BACKEND_URL}/apideck/session`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            consumer_id: consumerId,
            redirect_uri: BACKEND_URL,
            user_name: googleUser?.name || "Cliente Dansu",
            account_name: googleUser?.email || "Dansu Client"
          })
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Error creando la sesión de Vault");
        if (!data.session_uri) throw new Error("No se recibió session_uri del servidor");

        showStatus("Abriendo Apideck Vault... Completa la autorización.", "loading");

        const newWindow = window.open(data.session_uri, "_blank");
        if (!newWindow || newWindow.closed || typeof newWindow.closed === "undefined") {
          showStatus(`
            El navegador bloqueó la ventana emergente.<br><br>
            <a href="${data.session_uri}" target="_blank" style="color:#1e40af; font-weight:600; text-decoration:underline;">
              Haz clic aquí para abrir la conexión manualmente
            </a>
          `, "error");
        } else {
          showStatus("Ventana de conexión abierta. Autoriza el acceso y vuelve aquí.", "success");
        }
      } catch (error) {
        console.error("Error:", error);
        showStatus("Error: " + error.message, "error");
      } finally {
        btn.disabled = false;
        btn.innerHTML = "Conectar con CRM";
      }
    }

    async function checkConnection(isInitialLoad = false) {
      if (!isInitialLoad) showStatus("Comprobando conexión con el CRM...", "loading");

      try {
        const response = await fetch(`${BACKEND_URL}/apideck/connection-status/${consumerId}`);
        const data = await response.json();
        console.log("Estado actual de la conexión:", data);

        if (data.connected) {
          const crmName = data.name || "HubSpot";
          document.getElementById("crmBadge").style.display = "block";
          document.getElementById("crmBadge").innerHTML = `✅ CRM conectado: <strong>${crmName}</strong>`;
          document.getElementById("createAgentBtn").style.display = "inline-flex";
          document.getElementById("connectCrmBtn").style.display = "none";
          document.getElementById("resetBtn").style.display = "inline-flex";
          showStatus(`✅ <strong>${crmName}</strong> conectado correctamente`, "success");

          if (isInitialLoad) await syncSchema(true);
          return;
        }

        // Caso frecuente: state = available → intentamos habilitar
        if (data.state === "available") {
          showStatus("Conexión detectada (available). Intentando habilitarla...", "loading");
          try {
            await fetch(`${BACKEND_URL}/apideck/enable/${consumerId}`, { method: "POST" });
          } catch (e) {
            console.error("Error al habilitar:", e);
          }
          setTimeout(() => checkConnection(false), 1500);
          return;
        }

        if (!isInitialLoad) {
          showStatus("CRM todavía no está conectado. Estado: " + (data.state || "no conectado"), "error");
        }
      } catch (error) {
        console.error(error);
        if (!isInitialLoad) showStatus("Error comprobando estado: " + error.message, "error");
      }
    }

    async function syncSchema(silent = false) {
      if (!silent) showStatus("Solicitando estructura de la API del CRM...", "loading");

      try {
        const response = await fetch(`${BACKEND_URL}/apideck/sync-schema/${consumerId}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Error al sincronizar schema");

        if (!silent) showStatus("¡Estructura del CRM enviada a los logs de Render con éxito!", "success");
        console.log("Sync response:", data);
      } catch (error) {
        console.error(error);
        if (!silent) showStatus("Error al sincronizar schema: " + error.message, "error");
      }
    }

    function createAgent() {
      showStatus("Función 'Crear agente telefónico' lista. Próximamente se conectará con Retell.", "info");
    }

    async function resetAll() {
      const btn = document.getElementById("resetBtn");
      btn.disabled = true;
      btn.textContent = "Reiniciando...";
      showStatus("Eliminando conexión y cerrando sesión...", "loading");

      try {
        await fetch(`${BACKEND_URL}/apideck/disconnect/${consumerId}`, { method: "DELETE" });

        if (typeof google !== "undefined" && google.accounts) {
          google.accounts.id.disableAutoSelect();
        }

        localStorage.removeItem("dansu_consumer_id");
        consumerId = "user-" + Date.now();
        localStorage.setItem("dansu_consumer_id", consumerId);
        document.getElementById("consumerIdDisplay").textContent = consumerId;

        isGoogleLoggedIn = false;
        googleUser = null;

        document.getElementById("connectCrmBtn").style.display = "none";
        document.getElementById("createAgentBtn").style.display = "none";
        document.getElementById("crmBadge").style.display = "none";
        document.getElementById("divider").style.display = "none";
        document.getElementById("resetBtn").style.display = "none";

        document.getElementById("googleButtonContainer").innerHTML = "";
        initializeGoogleButton();

        showStatus("Todo reiniciado. Puedes empezar de nuevo.", "success");
        setTimeout(() => hideStatus(), 2500);
      } catch (error) {
        console.error("Error al reiniciar:", error);
        showStatus("Error al reiniciar: " + error.message, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "Volver a empezar";
      }
    }

    window.addEventListener("load", () => {
      initializeGoogleButton();
      setTimeout(() => checkConnection(true), 800);
    });
  </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)


# ============================================================
# FIN DEL BACKEND
# ============================================================
