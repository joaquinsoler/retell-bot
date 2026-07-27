from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel

app = FastAPI()

# Habilitar CORS para permitir peticiones desde tu web dansu.info
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # temporalmente permite todo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tu ID de cliente de Google Cloud
GOOGLE_CLIENT_ID = "667952866685-37b9ksse2l8krjo4t7t6tdhdqbk11e34.apps.googleusercontent.com"

class TokenRequest(BaseModel):
    token: str

@app.post("/api/auth/google")
def google_auth(body: TokenRequest):
    token = body.token
    
    if not token:
        print("❌ Error: No se ha proporcionado ningún token.")
        raise HTTPException(status_code=400, detail="No token provided")

    try:
        # Verificar el token con los servidores oficiales de Google
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)

        # Extraer los datos del usuario autenticado
        google_id = idinfo['sub']
        email = idinfo['email']
        name = idinfo.get('name', 'Sin nombre')
        picture = idinfo.get('picture', '')

        # Imprimir en la pantalla de logs con un mensaje de éxito claro
        print("--------------------------------------------------")
        print("🎉 ¡AUTENTICACIÓN CON GOOGLE EXITOSA! 🎉")
        print(f"• Nombre: {name}")
        print(f"• Correo electrónico: {email}")
        print(f"• Google ID: {google_id}")
        print(f"• Foto de perfil: {picture}")
        print("--------------------------------------------------")

        # El código se detiene y verifica aquí que ha alcanzado su objetivo con éxito
        return {
            "status": "success",
            "message": "Autenticación recibida e impresa en logs correctamente",
            "email": email
        }

    except ValueError as e:
        print(f"❌ Error de seguridad: Token de Google inválido -> {str(e)}")
        raise HTTPException(status_code=401, detail=f"Token de Google inválido: {str(e)}")
