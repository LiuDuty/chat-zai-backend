# ============================================================
#  SISTEMA DE CONVERSA INTELIGENTE (Z.ai + FastAPI) + BUSCA DE IMÓVEIS
#  Contexto incremental + Timeout estendido + Ping Render Free
#  CORS fixo + Integração real com API Z.ai + Lógica de busca
# ============================================================

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, asyncio, random, httpx, json
from contextlib import asynccontextmanager

# ------------------------------------------------------------
# 1️⃣ Configurações
# ------------------------------------------------------------
API_KEY = "03038b49c41b4bbdb1ce54888b54d223.cOjmjTibnl3uqERW"
API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
DB_FILE = "conversas.db"
DB_IMOBILIARIA = "imobiliaria.db" # Adicionando o DB de imóveis
RENDER_URL = "https://chatzai.onrender.com"
FRONTEND_URL = "https://chat-zai-frontend.vercel.app"

# Prompt para o assistente (persona)
SYSTEM_PROMPT = (
    """🔑 **Olá! Sou o OpenHouses** — seu assistente de consultoria exclusivo para imóveis de alto padrão!

🏙️ **Minha missão é transformar sua busca pelo imóvel dos sonhos em uma experiência sofisticada e eficiente:**
- Apresentar uma curadoria personalizada dos imóveis mais exclusivos, alinhados com seu estilo de vida e preferências.
- Oferecer insights detalhados sobre cada empreendimento, desde acabamentos de luxo até a valorização do bairro.
- Organizar e agendar visitas de forma discreta e conveniente, gerenciando sua agenda de forma inteligente.
- Auxiliar em todo o processo de negociação e burocracia, garantindo uma transação segura e bem-sucedida.

Vamos encontrar o seu próximo lar?"""
)

# Novo prompt para a IA interpretar a intenção de busca do usuário
INTERPRETATION_PROMPT = """
Você é um interpretador de consultas de imóveis. Sua ÚNICA tarefa é analisar a mensagem do usuário e extrair critérios de busca.
Retorne EXCLUSIVAMENTE um objeto JSON. Não adicione nenhum texto, explicação ou formatação além do JSON.
Se a mensagem não contiver nenhuma intenção de busca, retorne um objeto JSON vazio: {}.

Regras de Mapeamento:
- "bairros como [X, Y]" ou "em X ou Y" -> {"bairro_contem": ["X", "Y"]}
- "no bairro X" -> {"bairro": "X"}
- "até R$ 500mil" ou "máximo 500.000" -> {"valor_max": 500000}
- "acima de 300 mil" -> {"valor_min": 300000}
- "mais de 2 quartos" ou "pelo menos 3 dormitórios" -> {"dormitorios_min": 3}
- "no máximo 2 quartos" -> {"dormitorios_max": 2}
- "com suíte" -> {"suites_min": 1}
- "sem suíte" -> {"suites": 0}
- "com 2 vagas" -> {"vagas": 2}
- "tipo Apartamento" -> {"tipo": "Apartamento"}
- "tipo Casa" -> {"tipo": "Casa"}
- "em condomínio" -> {"em_condominio": true}
- "finalidade Aluguel" -> {"finalidade": "Aluguel"}
- "finalidade Venda" -> {"finalidade": "Venda"}

Exemplo de Entrada: "Quero um apartamento em Moema ou Vila Mariana, com no máximo 2 quartos e que custe até 800.000, sem suíte."
Exemplo de Saída Esperada: {"tipo": "Apartamento", "bairro_contem": ["Moema", "Vila Mariana"], "dormitorios_max": 2, "valor_max": 800000, "suites": 0}
"""

# ------------------------------------------------------------
# 2️⃣ Banco de dados (conversas)
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
# 3️⃣ Lógica de busca de imóveis (integrada do seu código)
# ------------------------------------------------------------
def buscar_imoveis_robusto(filtro_dicionario: dict) -> list[tuple]:
    """
    Versão robusta que limpa campos monetários formatados como string
    diretamente na consulta SQL para permitir comparações numéricas.
    Suporta filtros IN para listas e _contem para campos de texto múltiplos.
    Retorna: codigo_url, codigo_interno, valor.
    """
    conn = sqlite3.connect(DB_IMOBILIARIA)
    cursor = conn.cursor()

    # Seleciona os campos desejados na saída
    sql = "SELECT DISTINCT codigo_url, codigo_interno, valor FROM imoveis WHERE 1=1"
    params = []

    # Separamos os campos para aplicar a lógica correta
    campos_numericos = ['area_terreno', 'area_util', 'banheiros', 'dormitorios', 'suites', 'vagas']
    # Estes são os campos que estão como "R$ ... ,00" na tabela
    campos_monetarios = ['valor', 'iptu', 'valor_condominio']

    for campo, valor in filtro_dicionario.items():
        
        # --- Tratamento para filtros de MÍNIMO ---
        if campo.endswith('_min'):
            coluna = campo.replace('_min', '')
            
            if coluna in campos_monetarios:
                valor_numerico = str(valor).replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
                try:
                    valor_float = float(valor_numerico)
                except ValueError:
                    continue

                sql_coluna_limpa = f"CAST(REPLACE(REPLACE(REPLACE({coluna}, 'R$', ''), '.', ''), ',', '.') AS REAL)"
                sql += f" AND {sql_coluna_limpa} >= ?"
                params.append(valor_float)

            elif coluna in campos_numericos:
                sql += f" AND CAST({coluna} AS REAL) >= ?"
                params.append(valor)
            else:
                # LÓGICA para campos de texto normal (comparação alfabética)
                sql += f" AND {coluna} >= ?"
                params.append(valor)
        
        # --- Tratamento para filtros de MÁXIMO ---
        elif campo.endswith('_max'):
            coluna = campo.replace('_max', '')

            if coluna in campos_monetarios:
                valor_numerico = str(valor).replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
                try:
                    valor_float = float(valor_numerico)
                except ValueError:
                    continue

                sql_coluna_limpa = f"CAST(REPLACE(REPLACE(REPLACE({coluna}, 'R$', ''), '.', ''), ',', '.') AS REAL)"
                sql += f" AND {sql_coluna_limpa} <= ?"
                params.append(valor_float)

            elif coluna in campos_numericos:
                sql += f" AND CAST({coluna} AS REAL) <= ?"
                params.append(valor)
            else:
                # LÓGICA para campos de texto normal (comparação alfabética)
                sql += f" AND {coluna} <= ?"
                params.append(valor)

        # --- Tratamento para campos de texto que PODEM CONTER um dos valores (LIKE '%termo%') ---
        elif campo.endswith('_contem'):
            coluna = campo.replace('_contem', '')
            if isinstance(valor, list):
                likes = [f"{coluna} LIKE ?" for _ in valor]
                sql += f" AND ({' OR '.join(likes)})"
                params.extend([f"%{termo}%" for termo in valor])
            else:
                sql += f" AND {coluna} LIKE ?"
                params.append(f"%{valor}%")
                
        # --- Tratamento para campos com valor exato em uma lista (operador IN) ---
        elif isinstance(valor, list):
            placeholders = ', '.join(['?'] * len(valor))
            sql += f" AND {campo} IN ({placeholders})"
            params.extend(valor)
            
        # --- Tratamento para campos booleanos (Sim/Não) ---
        elif isinstance(valor, bool):
            if valor:
                sql += f" AND {campo} = ?"
                params.append("Sim")
            else:
                sql += f" AND ({campo} != ? OR {campo} IS NULL OR {campo} = '')"
                params.append("Sim")
                
        else: # Igualdade exata para um único valor
            sql += f" AND {campo} = ?"
            params.append(valor)

    print("--- Gerando SQL ---")
    print(f"Consulta: {sql}")
    print(f"Parâmetros: {params}")
    print("-------------------")

    cursor.execute(sql, params)
    resultados = cursor.fetchall()
    
    conn.close()
    
    return resultados

# ------------------------------------------------------------
# 4️⃣ Lógica principal: Interpretar, Buscar e Gerar Resposta
# ------------------------------------------------------------
async def atualizar_e_gerar_resposta(session_id: str, nova_mensagem: str):
    try:
        salvar_mensagem(session_id, "user", nova_mensagem, 9)
        contexto = buscar_contexto(session_id)

        # --- ETAPA 1: INTERPRETAR A INTENÇÃO DO USUÁRIO ---
        prompt_interpretacao = [
            {"role": "system", "content": INTERPRETATION_PROMPT},
            {"role": "user", "content": nova_mensagem},
        ]

        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        timeout_config = httpx.Timeout(120.0)

        async with httpx.AsyncClient(timeout=timeout_config) as client:
            resp_interpretacao = await client.post(API_URL, json={"model": "glm-4.5-flash", "messages": prompt_interpretacao}, headers=headers)

        if resp_interpretacao.status_code != 200:
            # Se a API falhar na interpretação, avisa e continua com uma conversa normal
            print(f"❌ Erro na API Z.ai (interpretação): {resp_interpretacao.text}")
            filtro_json = {}
        else:
            data_interpretacao = resp_interpretacao.json()
            resposta_interpretacao = data_interpretacao.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            try:
                filtro_json = json.loads(resposta_interpretacao)
            except json.JSONDecodeError:
                print(f"⚠️ A IA não retornou um JSON válido na interpretação: {resposta_interpretacao}")
                filtro_json = {}

        # --- ETAPA 2: BUSCAR NO BANCO (SE NECESSÁRIO) E GERAR RESPOSTA FINAL ---
        if filtro_json:
            # Se o filtro não estiver vazio, realiza a busca
            print(f"🔍 Filtro detectado: {filtro_json}")
            resultados_encontrados = buscar_imoveis_robusto(filtro_json)
            
            # Agora, pede à IA para formatar os resultados
            prompt_geracao = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"A pergunta original do usuário foi: '{nova_mensagem}'"},
                {"role": "user", "content": f"Com base nisso, realizei uma busca no banco de dados e obtive os seguintes resultados brutos (código_url, código_interno, valor):\n{resultados_encontrados}"},
                {"role": "user", "content": "Por favor, apresente esses resultados de forma clara e amigável para o usuário, sempre utilize o link https://www.openhouses.net.br/imovel/ e acrescente os codigos para usuario poder entrar nos links e ver as imagens. Se a lista de resultados estiver vazia, informe que nenhum imóvel foi encontrado com os critérios e sugira que ele ajuste a busca."}
            ]

            async with httpx.AsyncClient(timeout=timeout_config) as client:
                resp_geracao = await client.post(API_URL, json={"model": "glm-4.5-flash", "messages": prompt_geracao}, headers=headers)
            
            if resp_geracao.status_code != 200:
                resposta = f"❌ Erro ao gerar a resposta final com a API Z.ai: {resp_geracao.text}"
            else:
                data_geracao = resp_geracao.json()
                resposta = data_geracao.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        else:
            # Se o filtro estiver vazio, é uma conversa normal. Usa o contexto.
            prompt_conversa = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": f"Contexto até agora:\n{contexto}"},
                {"role": "user", "content": nova_mensagem},
            ]
            
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                resp_conversa = await client.post(API_URL, json={"model": "glm-4.5-flash", "messages": prompt_conversa}, headers=headers)

            if resp_conversa.status_code != 200:
                resposta = f"❌ Erro na API Z.ai (conversa): {resp_conversa.text}"
            else:
                data_conversa = resp_conversa.json()
                resposta = data_conversa.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        if not resposta:
            return "⚠️ Nenhuma resposta gerada pela API Z.ai."

        # Salva a resposta final e atualiza o contexto
        salvar_mensagem(session_id, "assistant", resposta, 9)
        novo_contexto = f"{contexto}\nUsuário: {nova_mensagem}\nAssistente: {resposta}".strip()
        if len(novo_contexto) > 4000:
            novo_contexto = novo_contexto[-4000:]
        salvar_mensagem(session_id, "system", novo_contexto, 2)

        return resposta

    except Exception as e:
        return f"💥 Erro interno no backend: {str(e)}"

# ------------------------------------------------------------
# 5️⃣ FastAPI + CORS
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
    title="Z.ai Conversa Inteligente (Contexto Incremental + Busca de Imóveis)",
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
# 6️⃣ Rotas
# ------------------------------------------------------------
@app.get("/")
async def home():
    return {"status": "✅ API Z.ai ativa com busca de imóveis integrada."}

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
# 7️⃣ Ping Render Free
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
