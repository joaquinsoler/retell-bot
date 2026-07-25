from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional
import json

app = FastAPI()

# CORS (necesario para el frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== CONFIGURACIÓN ==========
NANGO_API_KEY = os.getenv("NANGO_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
NANGO_API_URL = "https://api.nango.dev"

# ========== BASE DE DATOS ==========
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS nango_connections (
                id SERIAL PRIMARY KEY,
                connection_id VARCHAR(255) UNIQUE NOT NULL,
                provider_config_key VARCHAR(255),
                provider VARCHAR(100),
                tags JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("Tabla nango_connections lista")
    except Exception as e:
        print(f"Error creando tabla: {e}")

# Crear la tabla al arrancar
init_db()

# ========== MODELOS ==========
class SessionRequest(BaseModel):
    userId: Optional[str] = None
    email: Optional[str] = None

@app.post("/session-token")
async def create_session_token(body: SessionRequest):
    try:
        headers = {
            "Authorization": f"Bearer {NANGO_API_KEY}",
            "Content-Type": "application/json"
        }

        # Construimos los tags sin valores null
        tags = {
            "end_user_id": body.userId or f"user-{os.urandom(4).hex()}"
        }

        # Solo añadimos el email si realmente viene un valor
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
            print("Error de Nango:", response.text)
            raise HTTPException(status_code=500, detail=response.text)

        data = response.json()
        token = data.get("data", {}).get("token") or data.get("token")

        if not token:
            raise HTTPException(status_code=500, detail="No se recibio token de Nango")

        return {"sessionToken": token}

    except Exception as e:
        print(f"Error creando session token: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ========== WEBHOOK de Nango ==========
@app.post("/nango-webhook")
async def nango_webhook(request: Request):
    try:
        payload = await request.json()
        print("Webhook recibido de Nango:")
        print(json.dumps(payload, indent=2))

        if (
            payload.get("type") == "auth"
            and payload.get("operation") == "creation"
            and payload.get("success") is True
        ):
            connection_id = payload.get("connectionId")
            provider_config_key = payload.get("providerConfigKey")
            provider = payload.get("provider")
            tags = payload.get("tags") or {}

            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO nango_connections 
                (connection_id, provider_config_key, provider, tags)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (connection_id) DO NOTHING
            """, (
                connection_id,
                provider_config_key,
                provider,
                json.dumps(tags)
            ))

            conn.commit()
            cur.close()
            conn.close()

            print(f"Autenticacion Google guardada - connectionId: {connection_id}")

        return {"status": "ok"}

    except Exception as e:
        print(f"Error en webhook: {e}")
        return {"status": "error", "message": str(e)}

# ========== Health check ==========
@app.get("/")
async def root():
    return {"message": "Servidor Nango + Google OAuth funcionando correctamente"}
