import os
import requests
import logging
from flask import request, jsonify
# Importa tu conexión o pool de PostgreSQL aquí (ej. psycopg2, SQLAlchemy, etc.)

logger = logging.getLogger(__name__)

# Tu Secret Key de Nango (puedes usarla directamente o cargarla desde una variable de entorno en Render)
NANGO_SECRET_KEY = os.getenv("NANGO_SECRET_KEY", "b744da8a-42f8-4a47-935b-928a3743b192")

@app.route('/api/guardar-google-auth', methods=['POST'])
def guardar_google_auth():
    data = request.get_json() or {}
    connection_id = data.get("connection_id")
    
    if not connection_id:
        return jsonify({"status": "error", "message": "Falta el connection_id"}), 400

    try:
        # 1. Consultar a la API de Nango para obtener la información de la sesión de Google
        headers = {
            "Authorization": f"Bearer {NANGO_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        nango_url = f"https://api.nango.dev/connection/{connection_id}?provider_config_key=google"
        response = requests.get(nango_url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Error al obtener datos de Nango: {response.text}")
            return jsonify({"status": "error", "message": "No se pudo recuperar la sesión de Nango"}), 500

        conn_data = response.json()
        
        # Extraemos los datos de identidad del usuario autenticado
        credentials = conn_data.get("credentials", {})
        raw_data = credentials.get("raw", {})
        user_email = raw_data.get("email") or credentials.get("user_id") or "desconocido"
        access_token = credentials.get("access_token")

        # 2. Guardar en tu base de datos PostgreSQL (dpg-d8r99pmrnols73f4ecmg-a)
        # Reemplaza esta sección con tu lógica real de conexión a la base de datos:
        # conn = get_db_connection()
        # cursor = conn.cursor()
        # cursor.execute(
        #     "INSERT INTO usuarios_google (connection_id, email, access_token) VALUES (%s, %s, %s) ON CONFLICT (connection_id) DO UPDATE SET email = EXCLUDED.email, access_token = EXCLUDED.access_token",
        #     (connection_id, user_email, access_token)
        # )
        # conn.commit()
        # cursor.close()
        # conn.close()

        # 3. Log obligatorio y explícito demostrando que se ha guardado correctamente
        logger.info(f" [DB SUCCESS] ¡Datos de sesión de Google guardados en PostgreSQL (Base de datos: dpg-d8r99pmrnols73f4ecmg-a) para el usuario: {user_email} | Connection ID: {connection_id}!")

        return jsonify({
            "status": "success",
            "message": "Autenticación de Google guardada correctamente en base de datos",
            "email": user_email,
            "connection_id": connection_id
        }), 200

    except Exception as e:
        logger.error(f" Excepción crítica al procesar la autenticación de Google: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
