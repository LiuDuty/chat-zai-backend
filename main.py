# ============================================================
#  SISTEMA DE CONVERSA INTELIGENTE (Z.ai + FastAPI) + BUSCA DE IMÓVEIS
#  Contexto incremental + Timeout estendido + Ping Render Free
#  CORS fixo + Integração real com API Z.ai + Lógica de busca
#  Rate Limiting + Retry com Exponential Backoff + Cache + Debug Console + SQL Query
# ============================================================

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlite3, asyncio, random, httpx, json, time, hashlib
from contextlib import asynccontextmanager
from typing import Dict, Optional, Tuple, List
from datetime import datetime, timedelta

# ------------------------------------------------------------
# 1️⃣ Configurações
# ------------------------------------------------------------
API_KEY = "03038b49c41b4bbdb1ce54888b54d223.cOjmjTibnl3uqERW"
API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
DB_FILE = "conversas.db"
DB_IMOBILIARIA = "imobiliaria.db"
RENDER_URL = "https://chatzai.onrender.com"
FRONTEND_URL = "https://chat-zai-frontend.vercel.app"

# Configurações de Rate Limiting
MAX_REQUESTS_PER_MINUTE = 20  # Limite conservador para evitar o erro 1305
REQUESTS_TRACKER = {}  # Dict para rastrear requisições por IP/session
CACHE_EXPIRE_TIME = 300  # 5 minutos de cache para respostas
RESPONSE_CACHE = {}  # Cache para respostas recentes

# Configurações de Debug
DEBUG_MODE = True  # Ativa o modo de depuração

# Prompt para o assistente (persona) - SEMPRE DO CÓDIGO
SYSTEM_PROMPT = (
    """🔑 **Olá! Sou o OpenHouses** — seu assistente de consultoria exclusivo para imóveis de alto padrão!

🏙️ **Minha missão é transformar sua busca pelo imóvel dos sonhos em uma experiência sofisticada e eficiente:**
- Apresentar uma curadoria personalizada dos imóveis mais exclusivos, alinhados com seu estilo de vida e preferências.
- Oferecer insights detalhados sobre cada empreendimento, desde acabamentos de luxo até a valorização do bairro.
- Organizar e agendar visitas de forma discreta e conveniente, gerenciando sua agenda de forma inteligente.
- Auxiliar em todo o processo de negociação e burocracia, garantindo uma transação segura e bem-sucedida.

Vamos encontrar o seu próximo lar?"""
)

# Novo prompt para a IA interpretar a intenção de busca do usuário - SEMPRE DO CÓDIGO
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
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def salvar_mensagem(session_id, role, content):
    """Salva mensagens no banco de dados para manter o histórico"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO conversas (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content),
    )
    conn.commit()
    conn.close()

def buscar_historico_conversa(session_id, limite=20):
    """Busca o histórico completo da conversa para manter o contexto"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Busca as últimas mensagens da conversa em ordem cronológica
    c.execute(
        "SELECT role, content FROM conversas WHERE session_id=? ORDER BY timestamp DESC LIMIT ?",
        (session_id, limite),
    )
    resultados = c.fetchall()
    conn.close()
    
    # Inverte para ordem cronológica correta
    if resultados:
        return [{"role": role, "content": content} for role, content in reversed(resultados)]
    return []

# ------------------------------------------------------------
# 3️⃣ Rate Limiting e Cache
# ------------------------------------------------------------
def check_rate_limit(identifier: str) -> bool:
    """Verifica se o identificador (IP ou session_id) excedeu o limite de requisições"""
    now = time.time()
    minute_ago = now - 60
    
    # Inicializa o registro se não existir
    if identifier not in REQUESTS_TRACKER:
        REQUESTS_TRACKER[identifier] = []
    
    # Remove requisições antigas (mais de 1 minuto)
    REQUESTS_TRACKER[identifier] = [
        req_time for req_time in REQUESTS_TRACKER[identifier] if req_time > minute_ago
    ]
    
    # Verifica se excedeu o limite
    if len(REQUESTS_TRACKER[identifier]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    # Adiciona a requisição atual
    REQUESTS_TRACKER[identifier].append(now)
    return True

def get_cache_key(prompt_messages: list) -> str:
    """Gera uma chave de cache baseada no conteúdo das mensagens"""
    content = json.dumps(prompt_messages, sort_keys=True)
    return hashlib.md5(content.encode()).hexdigest()

def get_cached_response(cache_key: str) -> Optional[str]:
    """Recupera uma resposta do cache se ainda for válida"""
    if cache_key in RESPONSE_CACHE:
        timestamp, response = RESPONSE_CACHE[cache_key]
        if time.time() - timestamp < CACHE_EXPIRE_TIME:
            return response
        else:
            # Remove do cache se expirou
            del RESPONSE_CACHE[cache_key]
    return None

def cache_response(cache_key: str, response: str):
    """Armazena uma resposta no cache"""
    RESPONSE_CACHE[cache_key] = (time.time(), response)

async def make_api_request_with_retry(messages: list, max_retries=3) -> Tuple[bool, str]:
    """
    Faz requisição à API com retry e exponential backoff
    Retorna (sucesso, resposta_ou_erro)
    """
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    
    for attempt in range(max_retries):
        # Calcula o tempo de espera (exponential backoff)
        if attempt > 0:
            wait_time = 2 ** attempt + random.uniform(0, 1)
            print(f"Tentativa {attempt + 1}/{max_retries}. Aguardando {wait_time:.2f} segundos...")
            await asyncio.sleep(wait_time)
        
        try:
            timeout_config = httpx.Timeout(120.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                response = await client.post(
                    API_URL, 
                    json={"model": "glm-4.5-flash", "messages": messages}, 
                    headers=headers
                )
            
            if response.status_code == 200:
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                return True, content
            elif response.status_code == 429 or "1305" in response.text:
                # Rate limit exceeded
                error_msg = f"Limite de requisições excedido (tentativa {attempt + 1}/{max_retries})"
                print(f"❌ {error_msg}: {response.text}")
                if attempt == max_retries - 1:
                    return False, "Desculpe, estou recebendo muitas solicitações no momento. Por favor, tente novamente em alguns minutos."
            else:
                print(f"❌ Erro na API Z.ai (tentativa {attempt + 1}/{max_retries}): {response.text}")
                if attempt == max_retries - 1:
                    return False, f"Erro ao comunicar com a API: {response.text}"
        
        except Exception as e:
            print(f"❌ Exceção na requisição (tentativa {attempt + 1}/{max_retries}): {str(e)}")
            if attempt == max_retries - 1:
                return False, f"Erro de conexão com a API: {str(e)}"
    
    return False, "Não foi possível obter resposta após várias tentativas."

# ------------------------------------------------------------
# 4️⃣ Lógica de busca de imóveis (integrada do seu código)
# ------------------------------------------------------------
def buscar_imoveis_robusto(filtro_dicionario: dict) -> Tuple[list[tuple], str, List]:
    """
    Versão robusta que limpa campos monetários formatados como string
    diretamente na consulta SQL para permitir comparações numéricas.
    Suporta filtros IN para listas e _contem para campos de texto múltiplos.
    Retorna: (resultados, sql_query, params)
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
    
    return resultados, sql, params

# ------------------------------------------------------------
# 5️⃣ Lógica principal: Interpretar, Buscar e Gerar Resposta
# ------------------------------------------------------------
async def atualizar_e_gerar_resposta(session_id: str, nova_mensagem: str, client_ip: str = None):
    try:
        # Verifica rate limiting usando session_id ou IP
        identifier = session_id if session_id else client_ip
        if not check_rate_limit(identifier):
            return "Desculpe, você fez muitas solicitações recentemente. Por favor, aguarde um momento antes de tentar novamente.", {}, "", []
        
        # Salva a mensagem do usuário no histórico
        salvar_mensagem(session_id, "user", nova_mensagem)
        
        # Busca o histórico completo da conversa
        historico = buscar_historico_conversa(session_id)
        
        # --- ETAPA 1: INTERPRETAR A INTENÇÃO DO USUÁRIO ---
        prompt_interpretacao = [
            {"role": "system", "content": INTERPRETATION_PROMPT},
            {"role": "user", "content": nova_mensagem},
        ]
        
        # Verifica cache para a interpretação
        cache_key = get_cache_key(prompt_interpretacao)
        cached_response = get_cached_response(cache_key)
        
        if cached_response:
            print("Usando resposta em cache para interpretação")
            try:
                filtro_json = json.loads(cached_response)
            except json.JSONDecodeError:
                print(f"⚠️ Cache inválido para interpretação: {cached_response}")
                filtro_json = {}
        else:
            # Faz requisição à API com retry
            sucesso, resposta_interpretacao = await make_api_request_with_retry(prompt_interpretacao)
            
            if not sucesso:
                # Se falhar, continua com uma conversa normal
                filtro_json = {}
            else:
                try:
                    filtro_json = json.loads(resposta_interpretacao)
                    # Armazena no cache
                    cache_response(cache_key, resposta_interpretacao)
                except json.JSONDecodeError:
                    print(f"⚠️ A IA não retornou um JSON válido na interpretação: {resposta_interpretacao}")
                    filtro_json = {}

        # Inicializa variáveis de debug
        sql_query = ""
        sql_params = []
        
        # --- ETAPA 2: BUSCAR NO BANCO (SE NECESSÁRIO) E GERAR RESPOSTA FINAL ---
        if filtro_json:
            # Se o filtro não estiver vazio, realiza a busca
            print(f"🔍 Filtro detectado: {filtro_json}")
            resultados_encontrados, sql_query, sql_params = buscar_imoveis_robusto(filtro_json)
            
            # Monta o prompt com o histórico da conversa e os resultados da busca
            prompt_geracao = [
                {"role": "system", "content": SYSTEM_PROMPT},
            ]
            
            # Adiciona o histórico da conversa (exceto a última mensagem do usuário que já está incluída)
            prompt_geracao.extend(historico[:-1])
            
            # Adiciona a informação sobre a busca realizada
            prompt_geracao.append({
                "role": "system", 
                "content": f"Com base na última mensagem do usuário, realizei uma busca no banco de dados e obtive os seguintes resultados brutos (código_url, codigo_interno, valor):\n{resultados_encontrados}"
            })
            
            # Adiciona a instrução para formatar os resultados
            prompt_geracao.append({
                "role": "system", 
                "content": "Por favor, apresente esses resultados de forma clara e amigável para o usuário, sempre utilize o link https://www.openhouses.net.br/imovel/codigo_url ou use https://www.openhouses.net.br/imovel/?ref=codigo_interno . Se a lista de resultados estiver vazia, informe que nenhum imóvel foi encontrado com os critérios e sugira que ele ajuste a busca ou que pesquise diretamente no site da imobiliária https://www.openhouses.net.br/."
            })
            
            # Verifica cache para a geração
            cache_key = get_cache_key(prompt_geracao)
            cached_response = get_cached_response(cache_key)
            
            if cached_response:
                print("Usando resposta em cache para geração")
                resposta = cached_response
            else:
                # Faz requisição à API com retry
                sucesso, resposta = await make_api_request_with_retry(prompt_geracao)
                
                if sucesso:
                    # Armazena no cache
                    cache_response(cache_key, resposta)
                else:
                    resposta = resposta  # Já contém a mensagem de erro

        else:
            # Se o filtro estiver vazio, é uma conversa normal. Usa o histórico completo.
            prompt_conversa = [
                {"role": "system", "content": SYSTEM_PROMPT},
            ]
            
            # Adiciona todo o histórico da conversa
            prompt_conversa.extend(historico)
            
            # Verifica cache para a conversa
            cache_key = get_cache_key(prompt_conversa)
            cached_response = get_cached_response(cache_key)
            
            if cached_response:
                print("Usando resposta em cache para conversa")
                resposta = cached_response
            else:
                # Faz requisição à API com retry
                sucesso, resposta = await make_api_request_with_retry(prompt_conversa)
                
                if sucesso:
                    # Armazena no cache
                    cache_response(cache_key, resposta)
                else:
                    resposta = resposta  # Já contém a mensagem de erro

        if not resposta:
            resposta = "⚠️ Nenhuma resposta gerada pela API Z.ai."

        # Salva a resposta da IA no histórico
        salvar_mensagem(session_id, "assistant", resposta)

        # Retorna a resposta e as informações de depuração
        return resposta, filtro_json if filtro_json else {}, sql_query, sql_params

    except Exception as e:
        return f"💥 Erro interno no backend: {str(e)}", {}, "", []

# ------------------------------------------------------------
# 6️⃣ FastAPI + CORS
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
# 7️⃣ Rotas
# ------------------------------------------------------------
@app.get("/")
async def home():
    return {"status": "✅ API Z.ai ativa com busca de imóveis integrada e proteção contra rate limiting."}

@app.post("/mensagem")
async def mensagem(request: Request):
    # Obtém o IP do cliente para rate limiting
    client_ip = request.client.host
    if "x-forwarded-for" in request.headers:
        client_ip = request.headers["x-forwarded-for"].split(",")[0].strip()
    
    data = await request.json()
    texto = data.get("texto", "").strip()
    session_id = data.get("session_id", "sessao")

    if not texto:
        return {"resposta": "Por favor, envie uma mensagem válida."}

    resposta, filtro_json, sql_query, sql_params = await atualizar_e_gerar_resposta(session_id, texto, client_ip)
    
    # Prepara a resposta padrão
    response_data = {"resposta": resposta}
    
    # Se estiver em modo debug, inclui informações de depuração na resposta
    if DEBUG_MODE:
        debug_info = {
            "filtro_json": filtro_json,
            "mensagem_usuario": texto,
            "timestamp": datetime.now().isoformat()
        }
        
        # Adiciona informações da query SQL se houver busca
        if sql_query:
            debug_info.update({
                "sql_query": sql_query,
                "sql_params": sql_params,
                "sql_para_executar": f"{sql_query}\n-- Parâmetros: {sql_params}"
            })
        
        # Adiciona informações de depuração diretamente na resposta
        response_data["debug"] = debug_info
        
        # Adiciona informações de depuração no topo da resposta para fácil visualização
        if filtro_json:
            response_data["resposta"] = f"[DEBUG] Filtro: {json.dumps(filtro_json, indent=2)}\n\n{resposta}"
            if sql_query:
                response_data["resposta"] = f"[DEBUG] SQL: {sql_query}\n[DEBUG] Parâmetros: {sql_params}\n\n{response_data['resposta']}"
    
    return JSONResponse(content=response_data)

@app.get("/historico/{session_id}")
async def get_historico(session_id: str):
    return {"historico": buscar_historico_conversa(session_id)}

@app.get("/status")
async def status():
    """Endpoint para verificar o status do sistema e as estatísticas de cache"""
    cache_size = len(RESPONSE_CACHE)
    active_sessions = len(REQUESTS_TRACKER)
    return {
        "status": "online",
        "cache_size": cache_size,
        "active_sessions": active_sessions,
        "max_requests_per_minute": MAX_REQUESTS_PER_MINUTE,
        "debug_mode": DEBUG_MODE
    }

@app.post("/toggle-debug")
async def toggle_debug():
    """Endpoint para alternar o modo de depuração"""
    global DEBUG_MODE
    DEBUG_MODE = not DEBUG_MODE
    return {"debug_mode": DEBUG_MODE}

@app.get("/test-sql")
async def test_sql_endpoint():
    """
    Endpoint de teste para verificar a funcionalidade SQL
    Retorna um exemplo de query que pode ser executada no SQLite
    """
    exemplo_filtro = {
        "tipo": "Apartamento",
        "bairro": "Moema",
        "valor_max": 800000,
        "dormitorios_min": 2
    }
    
    _, sql_query, sql_params = buscar_imoveis_robusto(exemplo_filtro)
    
    return {
        "exemplo_filtro": exemplo_filtro,
        "sql_query": sql_query,
        "sql_params": sql_params,
        "sql_para_executar": f"{sql_query}\n-- Parâmetros: {sql_params}"
    }

@app.get("/ola")
async def ola_mundo():
    """Endpoint de teste para verificar se o sistema está funcionando"""
    debug_info = {
        "mensagem": "Olá! Este é um teste para verificar se o sistema está funcionando corretamente.",
        "timestamp": datetime.now().isoformat(),
        "status": "success"
    }
    
    return {
        "mensagem": "Olá! Este é um teste para verificar se o sistema está funcionando corretamente.",
        "debug": debug_info
    }

# ------------------------------------------------------------
# 8️⃣ Ping Render Free
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