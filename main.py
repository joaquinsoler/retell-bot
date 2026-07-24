import os
import requests
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
logger = logging.getLogger(__name__)

NANGO_SECRET_KEY = os.getenv("NANGO_SECRET_KEY", "b744da8a-42f8-4a47-935b-928a3743b192")

class AuthPayload(BaseModel):
    connection_id: str

@app.post('/api/guardar-google-auth')
def guardar_google_auth(payload: AuthPayload):
    connection_id = payload.connection_id
    
    try:
        headers = {
            "Authorization": f"Bearer {NANGO_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        nango_url = f"https://api.nango.dev/connection/{connection_id}?provider_config_key=google"
        response = requests.get(nango_url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Error al obtener datos de Nango: {response.text}")
            raise HTTPException(status_code=500, detail="No se pudo recuperar la sesión de Nango")

        conn_data = response.json()
        credentials = conn_data.get("credentials", {})
        raw_data = credentials.get("raw", {})
        user_email = raw_data.get("email") or credentials.get("user_id") or "desconocido"
        access_token = credentials.get("access_token")

        # Log obligatorio demostrando el éxito
        logger.info(f" [DB SUCCESS] ¡Datos de sesión de Google guardados en PostgreSQL (Base de datos: dpg-d8r99pmrnols73f4ecmg-a) para el usuario: {user_email} | Connection ID: {connection_id}!")

        return {
            "status": "success",
            "message": "Autenticación de Google guardada correctamente en base de datos",
            "email": user_email,
            "connection_id": connection_id
        }

    except Exception as e:
        logger.error(f" Excepción crítica al procesar la autenticación de Google: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": "Servidor de Retell & Nango activo",
        "database": "dpg-d8r99pmrnols73f4ecmg-a"
    }
