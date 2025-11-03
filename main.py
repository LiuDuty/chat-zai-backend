# ============================================================
#  SISTEMA DE CONVERSA INTELIGENTE (Z.ai + FastAPI)
#  Contexto incremental + Timeout estendido + Ping Render Free
#  CORS fixo + Integração real com API Z.ai
# ============================================================

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, asyncio, random, httpx
from contextlib import asynccontextmanager

# ------------------------------------------------------------
# 1️⃣ Configurações
# ------------------------------------------------------------
API_KEY = "03038b49c41b4bbdb1ce54888b54d223.cOjmjTibnl3uqERW"
API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
DB_FILE = "conversas.db"
RENDER_URL = "https://chatzai.onrender.com"
FRONTEND_URL = "https://quiz-azure.vercel.app"

SYSTEM_PROMPT = (
 """🎯 **Oi! Sou o QUIZ Azure** — seu assistente dedicado exclusivamente ao **Microsoft Azure Fundamentals (AZ-900)**!

📚 **Minha missão:**
- Criar simulados práticos para o exame AZ-900
- Explicar conceitos do Azure de forma clara
- Acompanhar seu progresso com estatísticas
- Dar dicas para o dia da prova
"""
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
        # Atualiza o contexto (remove anterior e insere novo)
        c.execute("DELETE FROM conversas WHERE session_id=? AND tipo_mensagem=2", (session_id,))
        c.execute(
            "INSERT INTO conversas (session_id, role, content, tipo_mensagem) VALUES (?, ?, ?, 2)",
            (session_id, "system", content),
        )
    else:
        # Insere mensagem normal
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
# 3️⃣ Lógica principal: enviar à Z.ai e atualizar contexto
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

        # Atualiza contexto salvo
        novo_contexto = f"{contexto}\nUsuário: {nova_mensagem}\nAssistente: {resposta}".strip()
        if len(novo_contexto) > 4000:
            novo_contexto = novo_contexto[-4000:]
        salvar_mensagem(session_id, "system", novo_contexto, 2)

        return resposta

    except Exception as e:
        return f"💥 Erro interno no backend: {str(e)}"

# ------------------------------------------------------------
# 4️⃣ FastAPI + CORS
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
        print("Tarefa de ping cancelada.")

app = FastAPI(
    title="Z.ai Conversa Inteligente (Contexto Incremental + Timeout)",
    lifespan=lifespan
)

allowed_origins = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    FRONTEND_URL,
]

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

# ------------------------------------------------------------
# 5️⃣ Rotas
# ------------------------------------------------------------
@app.get("/")
async def home():
    return {"status": "✅ API Z.ai ativa e mantendo contexto incremental."}

@app.post("/mensagem")
async def mensagem(request: Request):
    data = await request.json()
    texto = data.get("texto", "").strip()
    session_id = data.get("session_id", "sessao")

    if not texto:
        return {"resposta": "Por favor, envie uma mensagem válida."}

    resposta = await atualizar_e_gerar_resposta(session_id, texto)
    return {"resposta": resposta}

@app.get("/contexto/{session_id}")
async def get_contexto(session_id: str):
    return {"contexto": buscar_contexto(session_id)}

# ------------------------------------------------------------
# 6️⃣ Ping Render Free
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
