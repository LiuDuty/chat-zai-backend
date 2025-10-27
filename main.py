# ============================================================
#  SISTEMA DE CONVERSA INTELIGENTE (Z.ai + FastAPI)
#  Contexto incremental + Ping Render Free
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, os, requests
from dotenv import load_dotenv
import asyncio
import random
import httpx

# ------------------------------------------------------------
# 1️⃣ Configurações
# ------------------------------------------------------------
load_dotenv()
API_KEY = os.getenv("ZAI_API_KEY")
API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
DB_FILE = "conversas.db"
RENDER_URL = os.getenv("RENDER_URL")  # URL pública do backend Render

SYSTEM_PROMPT = (
    "Você é o KISS AZ-900, um assistente de estudos do exame Microsoft Azure Fundamentals (AZ-900). "
    "Responda de forma didática e mantenha coerência com o contexto da conversa."
)

FRONTEND_URLS = [
    "https://chat-zai-frontend.vercel.app",
    "http://localhost:4200"
]

# ------------------------------------------------------------
# 2️⃣ Banco de dados
# ------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT,
            content TEXT,
            tipo_mensagem INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

def salvar_mensagem(session_id, role, content, tipo):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if tipo == 2:
        c.execute("DELETE FROM conversas WHERE session_id=? AND tipo_mensagem=2", (session_id,))
        c.execute("INSERT INTO conversas (session_id, role, content, tipo_mensagem) VALUES (?, ?, ?, 2)",
                  (session_id, "system", content))
    else:
        c.execute("INSERT INTO conversas (session_id, role, content, tipo_mensagem) VALUES (?, ?, ?, 9)",
                  (session_id, role, content))
    conn.commit()
    conn.close()

def buscar_contexto(session_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT content FROM conversas WHERE session_id=? AND tipo_mensagem=2", (session_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else ""

# ------------------------------------------------------------
# 3️⃣ Função principal
# ------------------------------------------------------------
def atualizar_e_gerar_resposta(session_id: str, nova_mensagem: str):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    salvar_mensagem(session_id, "user", nova_mensagem, 9)
    contexto = buscar_contexto(session_id)
    prompt = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Contexto até agora:\n{contexto}"},
        {"role": "user", "content": nova_mensagem},
    ]
    resp = requests.post(API_URL, json={"model": "glm-4.5-flash", "messages": prompt}, headers=headers)
    if resp.status_code != 200:
        return f"Erro na resposta: {resp.text}"
    resposta = resp.json()["choices"][0]["message"]["content"].strip()
    salvar_mensagem(session_id, "assistant", resposta, 9)
    novo_contexto = (f"{contexto}\nUsuário: {nova_mensagem}\nAssistente: {resposta}").strip()
    if len(novo_contexto) > 4000:
        novo_contexto = novo_contexto[-4000:]
    salvar_mensagem(session_id, "system", novo_contexto, 2)
    return resposta

# ------------------------------------------------------------
# 4️⃣ FastAPI
# ------------------------------------------------------------
app = FastAPI(title="Z.ai Conversa Inteligente (Contexto Incremental)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_URLS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Mensagem(BaseModel):
    texto: str
    session_id: str

@app.get("/", methods=["GET", "HEAD"])
def home():
    return {"status": "API Z.ai ativa e mantendo contexto incremental."}

@app.post("/mensagem")
def receber_mensagem(mensagem: Mensagem):
    resposta = atualizar_e_gerar_resposta(mensagem.session_id, mensagem.texto)
    return {"resposta": resposta}

@app.get("/contexto/{session_id}")
def get_contexto(session_id: str):
    return {"contexto": buscar_contexto(session_id)}

@app.options("/mensagem")
async def options_mensagem():
    return {"message": "CORS OK"}

# ------------------------------------------------------------
# 5️⃣ Ping aleatório para Render Free
# ------------------------------------------------------------
async def ping_randomico():
    if not RENDER_URL:
        print("RENDER_URL não definido. Ping desativado.")
        return
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(RENDER_URL)
            except:
                pass
            await asyncio.sleep(random.randint(300, 600))  # 5 a 10 minutos

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(ping_randomico())
