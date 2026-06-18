import os, sys, subprocess, json, uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import requests

# Instalación automática de dependencias
def setup():
    for p in ["fastapi", "uvicorn", "requests"]:
        try: __import__(p)
        except ImportError: subprocess.check_call([sys.executable, "-m", "pip", "install", p])
setup()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

RETELL_API_KEY = os.getenv("RETELL_API_KEY")

def retell_api(method, endpoint, data=None):
    url = f"https://api.retellai.com{endpoint}"
    headers = {"Authorization": f"Bearer {RETELL_API_KEY}", "Content-Type": "application/json"}
    return requests.request(method, url, headers=headers, json=data).json()

@app.post("/create-retell-bot")
async def create_bot(req: Request):
    payload = await req.json()
    data = payload.get("data", payload)
    
    # 1. Crear LLM
    prompt = f"Eres el asistente virtual de {data.get('nombre_negocio')}. Responde de forma amable y profesional."
    llm = retell_api("POST", "/create-retell-llm", {"model": "gpt-4o-mini", "general_prompt": prompt})
    
    # 2. Crear Agente
    agent = retell_api("POST", "/create-agent", {
        "agent_name": f"Bot {data.get('nombre_negocio')}",
        "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]},
        "voice_id": "openai-Alloy"
    })
    return {"status": "success", "agent_id": agent["agent_id"]}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
