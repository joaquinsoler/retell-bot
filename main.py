import resend
from pydantic import BaseModel
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ==================== CONFIGURACIÓN ====================
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"
resend.api_key = os.getenv("RESEND_API_KEY")

security = HTTPBearer()

# ==================== FUNCIONES JWT ====================
def create_magic_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode = {"sub": email.lower().strip(), "exp": expire, "type": "magic"}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

# ==================== MODELO ====================
class MagicLinkRequest(BaseModel):
    email: str

# ==================== ENDPOINT MAGIC LINK ====================
@app.post("/auth/magic-link")
async def send_magic_link(request: MagicLinkRequest):
    email = request.email.strip().lower()
    
    if not email or "@" not in email:
        raise HTTPException(400, "Email inválido")

    # Verificar que tenga al menos un asistente
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as count FROM asistentes WHERE google_calendar_email = %s", (email,))
    result = cur.fetchone()
    cur.close()
    conn.close()

    if result['count'] == 0:
        return {"status": "success"}  # No revelamos información

    token = create_magic_token(email)
    login_url = f"https://www.dansu.info/area-cliente?token={token}"   # ← Cambia si tu URL es distinta

    try:
        resend.Emails.send({
            "from": "Dansu <no-reply@dansu.info>",
            "to": email,
            "subject": "Tu enlace para acceder al panel de Dansu",
            "html": f"""
                <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:30px;">
                    <h2 style="color:#0078FF;">Bienvenido a Dansu</h2>
                    <p>Haz clic en el siguiente botón para acceder a tu panel:</p>
                    <a href="{login_url}" style="display:inline-block;background:#0078FF;color:white;padding:16px 32px;border-radius:8px;text-decoration:none;font-weight:bold;">
                        Entrar al Panel
                    </a>
                    <p style="color:#666;font-size:14px;margin-top:25px;">Este enlace caduca en 15 minutos.</p>
                </div>
            """
        })
        return {"status": "success"}
    except Exception as e:
        print(f"Error Resend: {e}")
        raise HTTPException(500, "Error al enviar email")

# ==================== PROTEGER /get-user-bots ====================
@app.post("/get-user-bots")
async def get_user_bots(user_email: str = Depends(get_current_user)):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM asistentes WHERE google_calendar_email = %s ORDER BY id DESC;", (user_email,))
        bots = cur.fetchall()
        cur.close()
        conn.close()
        return {"status": "success", "bots": bots}
    except Exception as e:
        raise HTTPException(500, str(e))
