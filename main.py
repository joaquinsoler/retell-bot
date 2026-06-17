import subprocess, sys, os, json, uvicorn, requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta

# Auto-instalación de dependencias
def install():
    for p in ["fastapi", "uvicorn", "requests", "google-auth", "google-api-python-client"]:
        try: __import__(p.replace("-", "_"))
        except: subprocess.check_call([sys.executable, "-m", "pip", "install", p])
install()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- RUTAS ---

@app.get("/")
async def root():
    return {"status": "online", "routes": ["/create-retell-bot", "/verify-calendar-access", "/retell-check-and-book"]}

@app.post("/verify-calendar-access")
async def verify_calendar(request: Request):
    data = await request.json()
    cal_id = data.get("calendar_id")
    try:
        # Aquí hacemos la prueba real de conexión
        info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
        creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/calendar'])
        service = build('calendar', 'v3', credentials=creds)
        service.calendars().get(calendarId=cal_id).execute()
        return {"status": "ok"}
    except Exception as e:
        print(f"DEBUG: Error en validación: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.post("/create-retell-bot")
async def create_bot(request: Request):
    # ... (Tu lógica de creación de bot) ...
    return {"status": "success"}

@app.post("/retell-check-and-book")
async def handle_interaction(request: Request):
    # ... (Tu lógica de reserva) ...
    return {"status": "reservado"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
