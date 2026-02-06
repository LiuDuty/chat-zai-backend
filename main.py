# ============================================================
#  SISTEMA SIMPLIFICADO DE BUSCA DE IMÓVEIS (Z.ai + FastAPI)
#  VERSÃO CORRIGIDA: Logs detalhados + payload ajustado à API
# ============================================================

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlite3, asyncio, random, httpx, json, time, hashlib
from contextlib import asynccontextmanager
from typing import Dict, Optional, Tuple, List

# ------------------------------------------------------------
# 1️⃣ Configurações
# ------------------------------------------------------------
API_KEY = "03038b49c41b4bbdb1ce54888b54d223.cOjmjTibnl3uqERW"
API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
DB_IMOBILIARIA = "imobiliaria.db"
RENDER_URL = "https://chatzai.onrender.com"
FRONTEND_URL = "https://chat-zai-frontend.vercel.app"

# Configurações de Rate Limiting
MAX_REQUESTS_PER_MINUTE = 20
REQUESTS_TRACKER = {}
CACHE_EXPIRE_TIME = 300
RESPONSE_CACHE = {}
DEBUG_MODE = True

# Prompts
SYSTEM_PROMPT = (
    """🔑 **Olá! Sou o OpenHouses** — seu assistente de consultoria exclusivo para imóveis de alto padrão!"""
)

# PROMPT OTIMIZADO PARA FORÇAR A BUSCA (JSON MODE)
INTERPRETATION_PROMPT = """
Você é um extrator rigoroso de dados imobiliários. Analise a mensagem do usuário.
SUA MISSÃO: Extrair TODOS os critérios de busca mencionados.
Retorne APENAS um objeto JSON válido. Não use markdown, sem texto antes ou depois.

Regras de Mapeamento (Priorize estas chaves):
- "Quero", "Preciso", "Busco" indicam início de busca.
- "Apartamento", "Casa", "Terreno" -> {"tipo": "valor"}
- "Bairro X", "Em X", "Em X ou Y" -> {"bairro": "valor"} ou {"bairro_contem": ["X"]}
- "Até R$ 500 mil", "Máximo 500k" -> {"valor_max": 500000}
- "Acima de 300 mil", "Mínimo 300k" -> {"valor_min": 300000}
- "2 quartos", "3 dormitórios" -> {"dormitorios": 2}
- "Mais de 2 quartos", "Pelo menos 3" -> {"dormitorios_min": 3}
- "No máximo 2 quartos" -> {"dormitorios_max": 2}
- "1 suíte", "Com suite" -> {"suites": 1}
- "Mais de 1 suíte" -> {"suites_min": 1}
- "1 vaga", "2 vagas" -> {"vagas": 2}
- "Condomínio" -> {"em_condominio": true}

Caso CRÍTICO:
Se a mensagem for um cumprimento simples (ex: "olá", "oi", "bom dia", "obrigado", "tchau") SEM NENHUMA menção a imóveis, características ou valores, retorne {}.
Em QUALQUER outro caso, tente extrair o máximo possível de filtros.

Exemplo Entrada: "3 suites 4 quartos , Alphaville"
Exemplo Saída: {"suites": 3, "dormitorios": 4, "bairro": "Alphaville"}
"""

# ------------------------------------------------------------
# 2️⃣ Rate Limiting, Cache e Utilitários
# ------------------------------------------------------------
def check_rate_limit(identifier: str) -> bool:
    now = time.time()
    minute_ago = now - 60
    if identifier not in REQUESTS_TRACKER:
        REQUESTS_TRACKER[identifier] = []
    REQUESTS_TRACKER[identifier] = [t for t in REQUESTS_TRACKER[identifier] if t > minute_ago]
    if len(REQUESTS_TRACKER[identifier]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    REQUESTS_TRACKER[identifier].append(now)
    return True

def get_cache_key(prompt_messages: list) -> str:
    content = json.dumps(prompt_messages, sort_keys=True)
    return hashlib.md5(content.encode()).hexdigest()

def get_cached_response(cache_key: str) -> Optional[str]:
    if cache_key in RESPONSE_CACHE:
        timestamp, response = RESPONSE_CACHE[cache_key]
        if time.time() - timestamp < CACHE_EXPIRE_TIME:
            return response
        else:
            del RESPONSE_CACHE[cache_key]
    return None

def cache_response(cache_key: str, response: str):
    RESPONSE_CACHE[cache_key] = (time.time(), response)

# CORRIGIDO: Logs detalhados + payload ajustado à doc da Z.ai
async def make_api_request_with_retry(
    messages: list,
    max_retries: int = 2,
    use_json_mode: bool = False,
) -> Tuple[bool, str]:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    if not messages or not isinstance(messages, list):
        return False, "Erro interno: formato inválido"

    for attempt in range(max_retries):
        if attempt > 0:
            wait_time = 0.5 * attempt  # retry leve
            print(f"⏳ Retry {attempt + 1}/{max_retries}. Aguardando {wait_time:.2f}s...")
            await asyncio.sleep(wait_time)

        try:
            timeout_config = httpx.Timeout(20.0, connect=10.0)
            payload = {
                "model": "glm-4.5-flash",
                "messages": messages,
                "max_tokens": 512,
                "temperature": 0.2,
                "stream": False,
                "response_format": {"type": "json_object"} if use_json_mode else {"type": "text"},
            }

            print(f"📤 Enviando para Z.ai (tentativa {attempt + 1})...")
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                response = await client.post(API_URL, json=payload, headers=headers)

            print(f"📥 Status Code: {response.status_code}")
            print(f"📥 Corpo da resposta (raw): {response.text[:2000]}")

            if response.status_code == 200:
                data = response.json()
                choices = data.get("choices", [])
                if choices and isinstance(choices, list) and len(choices) > 0:
                    content = choices[0].get("message", {}).get("content", "").strip()
                    return True, content
                else:
                    print("❌ Resposta 200, mas 'choices' veio vazia ou em formato inesperado.")
                    return False, "Resposta da API sem conteúdo (choices vazio)."
            elif response.status_code == 401:
                print("❌ 401 Unauthorized: possível problema com API_KEY.")
                return False, "Erro de autenticação na API."
            elif response.status_code == 429:
                print("❌ 429 Rate Limit / Muitas requisições.")
                if attempt == max_retries - 1:
                    return False, "Muitas solicitações. Tente novamente em alguns instantes."
                await asyncio.sleep(2)
            else:
                print(f"❌ Erro não tratado: {response.status_code} | {response.text}")
                if attempt == max_retries - 1:
                    return False, f"Erro na API: {response.status_code} {response.text[:500]}"

        except httpx.TimeoutException as e:
            print(f"⏱️ Timeout ao chamar a API Z.ai: {e}")
            if attempt == max_retries - 1:
                return False, "O serviço demorou muito para responder (timeout)."
        except Exception as e:
            print(f"❌ Exceção ao chamar API Z.ai: {e}")
            if attempt == max_retries - 1:
                return False, f"Erro de conexão: {str(e)}"

    return False, "Falha ao obter resposta da API."

# NOVA FUNÇÃO: Formatação Instantânea em Python
def formatar_resposta_python(resultados: list, filtro: dict) -> str:
    if not resultados:
        return (
            f"❌ **Não encontrei imóveis** exatos para o filtro: `{filtro}`.\n\n"
            f"💡 *Tente relaxar alguns critérios "
            f"(ex: aumentar a faixa de preço ou remover o bairro específico).*"
        )

    total = len(resultados)

    intro = f"🏠 **Encontrei {total} imóveis** perfeitos para você!"
    if 'bairro' in filtro:
        intro += f" No bairro {filtro['bairro']}."
    intro += "\n\n"

    texto = intro
    resultados_para_mostrar = resultados[:5]

    for imovel in resultados_para_mostrar:
        # AQUI: ajuste os índices conforme a ordem das colunas na sua tabela 'imoveis'
        # Exemplo genérico:
        # imovel[0] -> id/codigo
        # imovel[1] -> tipo
        # imovel[2] -> bairro
        # imovel[3] -> valor
        try:
            codigo = imovel[6]
            tipo = imovel[16] if len(imovel) > 16 else "Imóvel"
            bairro = imovel[2] if len(imovel) > 2 else "Localização"
            valor = imovel[19] if len(imovel) > 19 else "Consulte"

            link = f"https://www.openhouses.net.br/imovel/?ref={codigo}"

            texto += f"🔹 **{tipo}** em {bairro}\n"
            texto += f"💰 Valor: {valor}\n"
            texto += f"🔗 [Ver detalhes]({link})\n\n"
        except IndexError:
            continue

    if total > 5:
        texto += f"_👉 E mais {total - 5} opções disponíveis. Refine sua busca para ver detalhes específicos._"

    return texto

# ------------------------------------------------------------
# 3️⃣ Lógica de busca de imóveis
# ------------------------------------------------------------
def buscar_imoveis_robusto(filtro_dicionario: dict) -> Tuple[list, str, List]:
    conn = sqlite3.connect(DB_IMOBILIARIA)
    cursor = conn.cursor()

    sql = "SELECT * FROM imoveis WHERE 1=1"
    params = []

    campos_numericos = ['area_terreno', 'area_util', 'banheiros', 'dormitorios', 'suites', 'vagas']
    campos_monetarios = ['valor', 'iptu', 'valor_condominio']

    for campo, valor in filtro_dicionario.items():
        # Mínimo
        if campo.endswith('_min'):
            coluna = campo.replace('_min', '')
            if coluna in campos_monetarios:
                val_num = str(valor).replace("R$", "").replace(".", "").replace(",", ".")
                try:
                    val_float = float(val_num)
                    sql_col = f"CAST(REPLACE(REPLACE(REPLACE({coluna}, 'R$', ''), '.', ''), ',', '.') AS REAL)"
                    sql += f" AND {sql_col} >= ?"
                    params.append(val_float)
                except ValueError:
                    pass
            elif coluna in campos_numericos:
                sql += f" AND CAST({coluna} AS REAL) >= ?"
                params.append(valor)

        # Máximo
        elif campo.endswith('_max'):
            coluna = campo.replace('_max', '')
            if coluna in campos_monetarios:
                val_num = str(valor).replace("R$", "").replace(".", "").replace(",", ".")
                try:
                    val_float = float(val_num)
                    sql_col = f"CAST(REPLACE(REPLACE(REPLACE({coluna}, 'R$', ''), '.', ''), ',', '.') AS REAL)"
                    sql += f" AND {sql_col} <= ?"
                    params.append(val_float)
                except ValueError:
                    pass
            elif coluna in campos_numericos:
                sql += f" AND CAST({coluna} AS REAL) <= ?"
                params.append(valor)

        # Contém texto (LIKE)
        elif campo.endswith('_contem'):
            coluna = campo.replace('_contem', '')
            if isinstance(valor, list):
                likes = [f"{coluna} LIKE ?" for _ in valor]
                sql += f" AND ({' OR '.join(likes)})"
                params.extend([f"%{termo}%" for termo in valor])
            else:
                sql += f" AND {coluna} LIKE ?"
                params.append(f"%{valor}%")

        # Lista (IN)
        elif isinstance(valor, list):
            placeholders = ', '.join(['?'] * len(valor))
            sql += f" AND {campo} IN ({placeholders})"
            params.extend(valor)

        # Booleano
        elif isinstance(valor, bool):
            if valor:
                sql += f" AND {campo} = ?"
                params.append("Sim")
            else:
                sql += f" AND ({campo} != ? OR {campo} IS NULL)"
                params.append("Sim")

        # Igualdade exata
        else:
            sql += f" AND {campo} = ?"
            params.append(valor)

    cursor.execute(sql, params)
    resultados = cursor.fetchall()
    conn.close()

    return resultados, sql, params

# ------------------------------------------------------------
# 4️⃣ Lógica Principal OTIMIZADA
# ------------------------------------------------------------
async def processar_mensagem(session_id: str, nova_mensagem: str, client_ip: str = None):
    try:
        identifier = session_id if session_id else client_ip
        if not check_rate_limit(identifier):
            return "Muitas solicitações. Aguarde um momento.", {}, "", "", []

        # 2. Interpretar intenção com JSON mode
        prompt_interpretacao = [
            {"role": "system", "content": INTERPRETATION_PROMPT},
            {"role": "user", "content": nova_mensagem},
        ]

        cache_key = get_cache_key(prompt_interpretacao)
        cached_interpretation = get_cached_response(cache_key)

        filtro_json = {}
        if cached_interpretation:
            try:
                filtro_json = json.loads(cached_interpretation)
            except Exception:
                pass
        else:
            sucesso, resp_int = await make_api_request_with_retry(
                prompt_interpretacao,
                use_json_mode=True,
            )
            if sucesso:
                try:
                    filtro_json = json.loads(resp_int)
                    cache_response(cache_key, resp_int)
                except Exception as e:
                    print(f"⚠️ Erro ao fazer parse do JSON da interpretação: {e}")
                    filtro_json = {}
            else:
                print(f"⚠️ Falha na interpretação: {resp_int}")

        if DEBUG_MODE:
            print(f"\n{'=' * 50}")
            print(f"🔍 [CMD] Usuário: {nova_mensagem}")
            print(f"🔍 [CMD] Filtro Interpretado: {filtro_json}")
            print(f"{'=' * 50}\n")

        if not filtro_json:
            # Modo conversa: chamar modelo em modo texto
            prompt_conversa = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": nova_mensagem},
            ]
            sucesso, resposta = await make_api_request_with_retry(
                prompt_conversa,
                use_json_mode=False,
            )
            if not sucesso:
                resposta = "Erro ao gerar resposta."
                print(f"❌ Erro no modo conversa: {resposta}")

            return resposta, {}, "", "", []

        # Modo busca de imóveis
        resultados, sql_query, sql_params = buscar_imoveis_robusto(filtro_json)
        resposta_formatada = formatar_resposta_python(resultados, filtro_json)

        return resposta_formatada, filtro_json, sql_query, sql_params, resultados

    except Exception as e:
        print(f"💥 Erro em processar_mensagem: {e}")
        return f"Erro interno: {str(e)}", {}, "", "", []

# ------------------------------------------------------------
# 5️⃣ FastAPI App
# ------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Iniciando API Simplificada...")
    ping_task = asyncio.create_task(ping_randomico())
    yield
    print("🛑 Desligando API...")
    ping_task.cancel()

app = FastAPI(title="OpenHouses Bot Simplificado", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Mensagem(BaseModel):
    texto: str
    session_id: str

@app.post("/mensagem")
async def mensagem(request: Request):
    client_ip = request.client.host
    data = await request.json()
    texto = data.get("texto", "").strip()
    session_id = data.get("session_id", "default")

    if not texto:
        return {"resposta": "Envie uma mensagem válida."}

    resposta, filtro, sql, params, resultados = await processar_mensagem(session_id, texto, client_ip)

    response_data = {"resposta": resposta}

    if DEBUG_MODE:
        debug_info = {
            "filtro": filtro,
            "sql": sql,
            "params": params,
            "qtd_resultados": len(resultados) if resultados else 0
        }
        response_data["debug"] = debug_info

    return JSONResponse(content=response_data)

@app.get("/status")
async def status():
    return {"status": "online", "mode": "simplified_no_db_history_v2"}

async def ping_randomico():
    if not RENDER_URL:
        return
    while True:
        try:
            async with httpx.AsyncClient() as client:
                await client.get(RENDER_URL)
                print("🔁 Ping keep-alive enviado.")
        except Exception:
            pass
        await asyncio.sleep(random.randint(300, 600))