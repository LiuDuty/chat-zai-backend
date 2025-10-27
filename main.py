# ============================================================
#  SISTEMA DE CONVERSA INTELIGENTE (Z.ai + FastAPI)
#  Contexto incremental (sem busca de histórico)
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, os, requests
from dotenv import load_dotenv

# ------------------------------------------------------------
# 1️⃣  Configurações
# ------------------------------------------------------------
load_dotenv()
API_KEY = os.getenv("ZAI_API_KEY")
API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
DB_FILE = "/data/conversas.db"


SYSTEM_PROMPT = (
    "Você é o KISS AZ-900, um assistente de estudos do exame Microsoft Azure Fundamentals (AZ-900). "
    "Responda de forma didática e mantenha coerência com o contexto da conversa."
)

# ------------------------------------------------------------
# 2️⃣  Banco de dados
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

    if tipo == 2:  # contexto (mantém 1 por sessão)
        c.execute("DELETE FROM conversas WHERE session_id=? AND tipo_mensagem=2", (session_id,))
        c.execute("INSERT INTO conversas (session_id, role, content, tipo_mensagem) VALUES (?, ?, ?, 2)",
                  (session_id, "system", content))
    else:  # histórico (apenas registro)
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
# 3️⃣  Função principal (sem busca de histórico)
# ------------------------------------------------------------
def atualizar_e_gerar_resposta(session_id: str, nova_mensagem: str):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    # Salva pergunta (tipo 9)
    salvar_mensagem(session_id, "user", nova_mensagem, 9)

    # Busca contexto existente
    contexto = buscar_contexto(session_id)

    # Prepara prompt de resposta com contexto atual
    prompt = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Contexto até agora:\n{contexto}"},
        {"role": "user", "content": nova_mensagem},
    ]

    # Gera resposta
    resp = requests.post(API_URL, json={"model": "glm-4.5-flash", "messages": prompt}, headers=headers)
    if resp.status_code != 200:
        return f"Erro na resposta: {resp.text}"

    resposta = resp.json()["choices"][0]["message"]["content"].strip()
    salvar_mensagem(session_id, "assistant", resposta, 9)

    # Atualiza contexto (incrementalmente)
    novo_contexto = (
        f"{contexto}\nUsuário: {nova_mensagem}\nAssistente: {resposta}"
    ).strip()

    # Opcional: limitar tamanho do contexto para não crescer demais
    if len(novo_contexto) > 4000:
        novo_contexto = novo_contexto[-4000:]  # mantém o mais recente

    salvar_mensagem(session_id, "system", novo_contexto, 2)

    return resposta

# ------------------------------------------------------------
# 4️⃣  API FastAPI
# ------------------------------------------------------------
app = FastAPI(title="Z.ai Conversa Inteligente (Contexto Incremental)")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:4200")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Mensagem(BaseModel):
    texto: str
    session_id: str

@app.get("/")
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
