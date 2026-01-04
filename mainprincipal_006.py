# ============================================================
#  SISTEMA SIMPLIFICADO DE BUSCA DE IMÓVEIS (Z.ai + FastAPI)
#  CORREÇÃO FORÇADA: Interpretação Agressiva na 1ª Interação
# ============================================================

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlite3, asyncio, random, httpx, json, time, hashlib
from contextlib import asynccontextmanager
from typing import Dict, Optional, Tuple, List

from mainprincipal import ping_randomico

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

# PROMPT OTIMIZADO PARA FORÇAR A BUSCA
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

async def make_api_request_with_retry(messages: list, max_retries=3) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    
    if not messages or not isinstance(messages, list):
        return False, "Erro interno: formato inválido"

    for attempt in range(max_retries):
        if attempt > 0:
            wait_time = 2 ** attempt + random.uniform(0, 1)
            print(f"⏳ Retry {attempt + 1}/{max_retries}. Aguardando {wait_time:.2f}s...")
            await asyncio.sleep(wait_time)
        
        try:
            timeout_config = httpx.Timeout(60.0)
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
            elif response.status_code == 429:
                print(f"❌ Rate Limit (429): {response.text}")
                if attempt == max_retries - 1:
                    return False, "Muitas solicitações. Tente em minutos."
            else:
                print(f"❌ Erro API: {response.text}")
                if attempt == max_retries - 1:
                    return False, f"Erro na API: {response.text}"
        
        except Exception as e:
            print(f"❌ Exceção: {str(e)}")
            if attempt == max_retries - 1:
                return False, f"Erro de conexão: {str(e)}"
    
    return False, "Falha ao obter resposta."

# ------------------------------------------------------------
# 3️⃣ Lógica de busca de imóveis
# ------------------------------------------------------------
def buscar_imoveis_robusto(filtro_dicionario: dict) -> Tuple[list, str, List]:
    conn = sqlite3.connect(DB_IMOBILIARIA)
    cursor = conn.cursor()

    sql = "SELECT codigo_interno FROM imoveis WHERE 1=1"
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
                except ValueError: pass
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
                except ValueError: pass
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
# 4️⃣ Lógica Principal Corrigida
# ------------------------------------------------------------
async def processar_mensagem(session_id: str, nova_mensagem: str, client_ip: str = None):
    try:
        # 1. Rate Limit
        identifier = session_id if session_id else client_ip
        if not check_rate_limit(identifier):
            return "Muitas solicitações. Aguarde um momento.", {}, "", "", []

        # 2. Interpretar Intenção (IA)
        prompt_interpretacao = [
            {"role": "system", "content": INTERPRETATION_PROMPT},
            {"role": "user", "content": nova_mensagem},
        ]
        
        cache_key = get_cache_key(prompt_interpretacao)
        cached_interpretation = get_cached_response(cache_key)
        
        filtro_json = {}
        if cached_interpretation:
            try: filtro_json = json.loads(cached_interpretation)
            except: pass
        else:
            sucesso, resp_int = await make_api_request_with_retry(prompt_interpretacao)
            if sucesso:
                # --- CORREÇÃO CRÍTICA: Limpeza de Markdown ---
                try:
                    # Remove ```json e ``` se a IA incluir
                    clean_resp = resp_int.replace('```json', '').replace('```', '').strip()
                    filtro_json = json.loads(clean_resp)
                    cache_response(cache_key, resp_int) # Salva o bruto no cache, limpa no load
                except json.JSONDecodeError as e:
                    print(f"⚠️ ERRO JSON Parse: {e}")
                    print(f"⚠️ Resposta Bruta da IA: {resp_int}")
                    filtro_json = {}

        # 3. Exibir filtro no Console (CMD)
        print(f"\n{'='*50}")
        print(f"🔍 [CMD] Usuário: {nova_mensagem}")
        print(f"🔍 [CMD] Filtro Interpretado: {filtro_json}")
        print(f"{'='*50}\n")

        # 4. Decisão: É uma busca ou conversa?
        if not filtro_json:
            # Modo Conversa Padrão
            prompt_conversa = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": nova_mensagem}
            ]
            sucesso, resposta = await make_api_request_with_retry(prompt_conversa)
            if not sucesso: resposta = "Erro ao gerar resposta."
            return resposta, {}, "", "", []

        # 5. Modo Busca de Imóveis
        resultados, sql_query, sql_params = buscar_imoveis_robusto(filtro_json)
        
        # LOG DA QUERY SQL NO CMD
        print(f"🔍 [CMD] Query SQL Gerada:")
        print(f"{sql_query}")
        print(f"🔍 [CMD] Parâmetros: {sql_params}")
        print(f"{'='*50}\n")

        # 6. Verificação se há dados
        if not resultados:
            resposta_direta = "não temos informacões no momento, consulte diretamente no site"
            return resposta_direta, filtro_json, sql_query, sql_params, []
        
        # 7. Se houver dados, IA formata
        mensagem_contexto = f"""
        Encontrei {len(resultados)} imóveis para o filtro: {filtro_json}.
        Dados brutos: {resultados}
        Formate isso para o usuário de forma amigável, usando links https://www.openhouses.net.br/imovel/?ref=codigo_interno.
        """
        
        prompt_formatar = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": nova_mensagem},
            {"role": "assistant", "content": mensagem_contexto}
        ]
        
        sucesso, resposta_formatada = await make_api_request_with_retry(prompt_formatar)
        if sucesso:
            return resposta_formatada, filtro_json, sql_query, sql_params, resultados
        else:
            return "Erro ao formatar resultados.", filtro_json, sql_query, sql_params, []

    except Exception as e:
        print(f"💥 Erro: {e}")
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
