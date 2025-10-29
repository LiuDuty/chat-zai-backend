# ============================================================
# SISTEMA DE CONVERSA INTELIGENTE (Z.ai + FastAPI)
# Contexto incremental + Timeout estendido + Ping Render Free
# CORS com valores fixos (para diagnóstico)
# ============================================================

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, os, asyncio, random, httpx
# 'load_dotenv' e 'dotenv' não são mais necessários
# from dotenv import load_dotenv
from contextlib import asynccontextmanager

# ------------------------------------------------------------
# 1️⃣ Configurações
# ------------------------------------------------------------
# --- MUDANÇA: Valores "hardcoded" no lugar de variáveis de ambiente ---
API_KEY = "03038b49c41b4bbdb1ce54888b54d223.cOjmjTibnl3uqERW"
API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
DB_FILE = "conversas.db"
RENDER_URL = "https://chat-zai-backend.onrender.com"
FRONTEND_URL = "https://chat-zai-frontend.vercel.app"
# --------------------------------------------------------------------

print(f"🔍 DEBUG 1: A URL do Frontend (hardcoded) é: {FRONTEND_URL}")

SYSTEM_PROMPT = (
    "Você é o KISS AZ-900, um assistente de estudos do exame Microsoft Azure Fundamentals (AZ-900). "
    "Responda de forma didática, clara e coerente com o contexto da conversa."
)

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
        c.execute(
            "INSERT INTO conversas (session_id, role, content, tipo_mensagem) VALUES (?, ?, ?, 2)",
            (session_id, "system", content),
        )
    else:
        c.execute(
            "INSERT INTO conversas (session_id, role, content, tipo_mensagem) VALUES (?, ?, ?, 9)",
            (session_id, role, content),
        )
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
# 3️⃣ Função principal (assíncrona com timeout)
# ------------------------------------------------------------
async def atualizar_e_gerar_resposta(session_id: str, nova_mensagem: str):
    try:
        salvar_mensagem(session_id, "user", nova_mensagem, 9)
        contexto = buscar_contexto(session_id)

        prompt = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"Contexto até agora:\n{contexto}"},
            {"role": "user", "content": nova_mensagem},
        ]

        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        timeout_config = httpx.Timeout(120.0)

        async with httpx.AsyncClient(timeout=timeout_config) as client:
            resp = await client.post(API_URL, json={"model": "glm-4.5-flash", "messages": prompt}, headers=headers)

        if resp.status_code != 200:
            return f"❌ Erro na API Z.ai: {resp.text}"

        data = resp.json()
        resposta = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        if not resposta:
            return "⚠️ Nenhuma resposta gerada pela API Z.ai."

        salvar_mensagem(session_id, "assistant", resposta, 9)

        novo_contexto = f"{contexto}\nUsuário: {nova_mensagem}\nAssistente: {resposta}".strip()
        if len(novo_contexto) > 4000:
            novo_contexto = novo_contexto[-4000:]
        salvar_mensagem(session_id, "system", novo_contexto, 2)
        return resposta

    except Exception as e:
        return f"💥 Erro interno no backend: {str(e)}"

# ------------------------------------------------------------
# 4️⃣ FastAPI + CORS com valores fixos
# ------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Aplicação está iniciando...")
    ping_task = asyncio.create_task(ping_randomico())
    yield
    print("🛑 Aplicação está sendo desligada.")
    ping_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        print("Tarefa de ping cancelada com sucesso.")


app = FastAPI(title="Z.ai Conversa Inteligente (Contexto Incremental + Timeout)", lifespan=lifespan)

@app.middleware("http")
async def log_response_headers(request: Request, call_next):
    response = await call_next(request)
    print(f"🌐 DEBUG 3: Resposta para {request.method} {request.url.path} com headers: {dict(response.headers)}")
    return response

# --- MUDANÇA: Usando a lista com a URL fixa ---
allowed_origins = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    FRONTEND_URL,  # Agora a URL é fixa no código
]

print(f"🔍 DEBUG 2: A lista final de origens permitidas para o CORS é: {allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Mensagem(BaseModel):
    texto: str
    session_id: str

@app.get("/")
async def home():
    return {"status": "✅ API Z.ai ativa e mantendo contexto incremental."}

@app.post("/mensagem")
async def mensagem(request: Request):
    data = await request.json()
    texto = data.get("texto", "")
    session_id = data.get("session_id", "sessao")

    if not texto:
        return {"resposta": "Por favor, envie uma mensagem válida."}

    resposta = f"Você disse: {texto}"
    return {"resposta": resposta}

@app.get("/contexto/{session_id}")
async def get_contexto(session_id: str):
    return {"contexto": buscar_contexto(session_id)}

# ------------------------------------------------------------
# 5️⃣ Ping aleatório (Render Free)
# ------------------------------------------------------------
async def ping_randomico():
    if not RENDER_URL:
        print("⚠️ RENDER_URL não definido. Ping desativado.")
        return
    while True:
        try:
            async with httpx.AsyncClient() as client:
                await client.get(RENDER_URL)
                print("🔁 Ping enviado para manter ativo.")
        except Exception as e:
            print(f"Erro no ping: {e}")
        await asyncio.sleep(random.randint(300, 600))