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
@app.get("/apideck/sync-schema/{consumer_id}")
async def sync_crm_schema_to_logs(consumer_id: str):
    """
    Recoge schema nativo de HubSpot → resume por partes con Grok → 
    mini-consolidaciones + consolidación final limpia y robusta.
    """
    import json
    import time
    import re

    logger.info(f"=== INICIO RESUMEN PROFESIONAL CON GROK | consumer: {consumer_id} ===")

    GROK_API_KEY = os.getenv("GROK_API_KEY")
    if not GROK_API_KEY:
        raise HTTPException(status_code=500, detail="Falta la variable de entorno GROK_API_KEY")

    # -------------------------------------------------
    # 1. Verificar conexión
    # -------------------------------------------------
    status = await check_hubspot_connection(consumer_id)
    if not status.get("connected"):
        raise HTTPException(status_code=400, detail="El CRM no está conectado o listo todavía.")

    service_id = status.get("service_id", "hubspot")
    if service_id != "hubspot":
        raise HTTPException(status_code=400, detail="Esta versión solo soporta HubSpot nativo")

    headers_proxy = {
        "Authorization": f"Bearer {APIDECK_API_KEY}",
        "x-apideck-app-id": APIDECK_APP_ID,
        "x-apideck-consumer-id": consumer_id,
        "x-apideck-service-id": "hubspot",
        "Content-Type": "application/json",
    }

    hubspot_objects = {
        "contacts": "contacts",
        "companies": "companies",
        "opportunities": "deals",
        "leads": "leads"
    }

    full_schema = {
        "crm": "HubSpot",
        "service_id": service_id,
        "consumer_id": consumer_id,
        "resources": {}
    }

    # -------------------------------------------------
    # 2. Recoger schema nativo
    # -------------------------------------------------
    for unified_name, hubspot_object in hubspot_objects.items():
        logger.info(f"Obteniendo '{hubspot_object}'...")

        resource_info = {
            "hubspot_object": hubspot_object,
            "native_properties": None,
            "sample_unified": None
        }

        try:
            downstream_url = f"https://api.hubapi.com/crm/v3/properties/{hubspot_object}"
            res = requests.get(
                f"{APIDECK_BASE}/proxy",
                headers={**headers_proxy, "x-apideck-downstream-url": downstream_url},
                timeout=20
            )
            if res.status_code < 400:
                data = res.json()
                resource_info["native_properties"] = data.get("results", data)
                logger.info(f"  ✓ Propiedades nativas de '{hubspot_object}'")
        except Exception as e:
            logger.error(f"  ✗ Error propiedades '{hubspot_object}': {e}")

        try:
            headers_unified = {**apideck_headers(consumer_id), "x-apideck-service-id": "hubspot"}
            res = requests.get(
                f"{APIDECK_BASE}/crm/{unified_name}",
                headers=headers_unified,
                params={"limit": 3},
                timeout=12
            )
            if res.status_code < 400:
                resource_info["sample_unified"] = res.json().get("data", [])
        except Exception as e:
            logger.warning(f"  - Error sample '{unified_name}': {e}")

        full_schema["resources"][unified_name] = resource_info

    # -------------------------------------------------
    # 3. Preparar texto y dividir
    # -------------------------------------------------
    schema_text = json.dumps(full_schema, ensure_ascii=False, default=str)
    total_chars = len(schema_text)
    logger.info(f"Schema original: {total_chars:,} caracteres")

    CHUNK_SIZE = 42000
    chunks = [schema_text[i:i + CHUNK_SIZE] for i in range(0, len(schema_text), CHUNK_SIZE)]
    total_chunks = len(chunks)
    logger.info(f"Dividido en {total_chunks} partes")

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------
    def call_grok(prompt: str, max_tokens: int = 3500) -> str:
        try:
            response = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "grok-3",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": max_tokens
                },
                timeout=120
            )
            if response.status_code >= 400:
                logger.error(f"Error Grok: {response.status_code} - {response.text[:300]}")
                return ""
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Exception Grok: {e}")
            return ""

    def clean_and_validate_json(text: str) -> str:
        """Limpieza robusta + validación real de JSON."""
        if not text:
            return ""

        text = text.strip()

        # Quitar bloques ```json
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()

        # Extraer desde el primer { hasta el último }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        try:
            parsed = json.loads(text)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"JSON final inválido: {e}")
            return text  # Devolvemos crudo para no perderlo

    def build_chunk_prompt(chunk: str) -> str:
        return f"""
Eres un experto en compactación de schemas de propiedades de HubSpot para agentes de voz (Retell AI).

Extrae SOLO la información mínima pero completa que un asistente telefónico necesita para buscar, crear y actualizar registros.

### Reglas absolutas
1. NUNCA elimines:
   - name, label, type
   - options de enums importantes (lifecyclestage, hs_lead_status, dealstage, pipeline, dealtype...)
   - Todos los CUSTOM FIELDS del cliente
   - Campos de identificación (hs_object_id)
   - Campos de relación (associatedcompanyid, hubspot_owner_id)
   - required y readOnly (usa false si no se sabe)

2. SÍ elimina:
   - Descripciones largas
   - Listas enormes de opciones (idiomas, timezones, países...)
   - Campos hs_* internos (excepto hs_lead_status y hs_object_id)
   - Campos hidden o puramente analíticos

3. Formato de salida (solo JSON):
{{
  "contacts": {{ "fields": [{{ "name": "...", "label": "...", "type": "...", "required": false, "readOnly": false }}] }},
  "companies": {{ "fields": [...] }},
  "deals": {{ "fields": [...] }},
  "leads": {{ "fields": [...] }}
}}

### Fragmento:
---
{chunk}
---
"""

    def build_consolidation_prompt(content: str, is_final: bool = False) -> str:
        extra = ""
        if is_final:
            extra = """
### REGLAS EXTRA OBLIGATORIAS PARA EL RESUMEN FINAL

6. Si "leads" está vacío o incompleto → reconstruye obligatoriamente:
   hs_object_id, firstname, lastname, email, phone, mobilephone, company, lifecyclestage, hs_lead_status, hubspot_owner_id

7. Incluye SIEMPRE esta sección exacta:
"associations": {
  "contacts": ["companies", "deals"],
  "companies": ["contacts", "deals"],
  "deals": ["contacts", "companies"],
  "leads": ["contacts", "companies"]
}

8. ELIMINA SIN PIEDAD:
   - Todos los hs_num_*
   - hs_email_optout_* y campos de opt-out
   - date_of_birth, degree, field_of_study, gender, graduation_date, company_size
   - fax, hs_reason_to_reach_out, hs_csm_sentiment, hs_domain_status, hs_quick_context
   - Campos de analytics e engagement internos

9. NO mezcles campos entre objetos:
   - annualrevenue, industry, numberofemployees, founded_year → SOLO companies
   - amount, dealstage, closedate, closed_won_reason, closed_lost_reason, dealtype, dealname → SOLO deals

10. CAMPOS OBLIGATORIOS que DEBEN aparecer:
   - contacts: hs_object_id, firstname, lastname, email, phone, mobilephone, company, jobtitle, lifecyclestage, hs_lead_status, hubspot_owner_id + custom fields
   - companies: hs_object_id, name, domain, phone, website, address, city, state, zip, country, annualrevenue, numberofemployees, type, hubspot_owner_id
   - deals: hs_object_id, dealname, amount, dealstage, pipeline, closedate, dealtype, closed_won_reason, closed_lost_reason, hubspot_owner_id, associatedcompanyid, hs_priority
   - leads: hs_object_id, firstname, lastname, email, phone, company, lifecyclestage, hs_lead_status, hubspot_owner_id

11. Conserva SIEMPRE los custom fields del cliente con sus options.
"""

        return f"""
Eres un experto senior en diseño de schemas limpios para agentes de voz (Retell AI) que controlan HubSpot.

Genera el resumen {"FINAL DEFINITIVO" if is_final else "intermedio"} más limpio, preciso y útil posible.

### REGLAS ESTRICTAS
1. Elimina TODAS las repeticiones.
2. Unifica contacts, companies, deals y leads.
3. Cada objeto DEBE tener al menos "hs_object_id".
4. Todos los campos deben tener "required" y "readOnly" (false si no se sabe).
5. Conserva SIEMPRE los custom fields del cliente.
{extra}

Devuelve ÚNICAMENTE el JSON final válido.
Sin markdown, sin explicaciones, sin ```json.

RESUMEN A CONSOLIDAR:
{content}
"""

    # -------------------------------------------------
    # 4. Resumir por partes + mini-consolidaciones
    # -------------------------------------------------
    accumulated_summary = ""

    for idx, chunk in enumerate(chunks, 1):
        logger.info(f"Resumiendo parte {idx}/{total_chunks}...")

        part_summary = call_grok(build_chunk_prompt(chunk))

        if not part_summary:
            logger.warning(f"  ⚠ Parte {idx} falló")
            continue

        if not accumulated_summary:
            accumulated_summary = part_summary
        else:
            accumulated_summary += "\n\n--- NUEVA PARTE ---\n\n" + part_summary

        logger.info(f"  ✓ Parte {idx}/{total_chunks} completada")

        # Mini-consolidación cada 5 partes
        if idx % 5 == 0 and idx < total_chunks:
            logger.info(f"  → Mini-consolidación en parte {idx}...")
            mini = call_grok(build_consolidation_prompt(accumulated_summary, is_final=False), max_tokens=4000)
            if mini:
                accumulated_summary = mini
                logger.info("  ✓ Mini-consolidación completada")

        time.sleep(1.1)

    # -------------------------------------------------
    # 5. Consolidación final + reintento
    # -------------------------------------------------
    logger.info("Realizando consolidación final...")

    final_raw = call_grok(build_consolidation_prompt(accumulated_summary, is_final=True), max_tokens=8000)

    if not final_raw or len(final_raw) < 800:
        logger.warning("Consolidación final débil o vacía → reintentando una vez...")
        time.sleep(2)
        final_raw = call_grok(build_consolidation_prompt(accumulated_summary, is_final=True), max_tokens=8000)

    if not final_raw:
        final_raw = accumulated_summary

    final_summary = clean_and_validate_json(final_raw)

    # -------------------------------------------------
    # 6. Resultado (logs divididos)
    # -------------------------------------------------
    final_chars = len(final_summary)
    final_tokens = final_chars / 4

    logger.info("\n" + "=" * 90)
    logger.info("📄 RESUMEN FINAL CONSOLIDADO Y LIMPIO")
    logger.info("=" * 90)

    chunk_size = 3000
    for i in range(0, len(final_summary), chunk_size):
        part_num = i // chunk_size + 1
        logger.info(f"--- PARTE {part_num} DEL RESUMEN ---")
        logger.info(final_summary[i:i + chunk_size])

    logger.info("=" * 90)
    logger.info(f"Tamaño final: {final_chars:,} caracteres ≈ {final_tokens:,.0f} tokens")
    logger.info(f"Reducción total: de {total_chars:,} → {final_chars:,} caracteres")
    logger.info("=" * 90)
    logger.info(f"=== PROCESO COMPLETADO | consumer: {consumer_id} ===\n")

    return {
        "success": True,
        "message": "Resumen profesional consolidado generado. Revisa los logs.",
        "original_characters": total_chars,
        "final_characters": final_chars,
        "final_tokens_estimated": int(final_tokens),
        "chunks_processed": total_chunks,
        "summary": final_summary
    }
@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "dansu-retell-bot",
        "google_configured": bool(GOOGLE_CLIENT_ID),
        "apideck_configured": bool(APIDECK_API_KEY and APIDECK_APP_ID)
    }
