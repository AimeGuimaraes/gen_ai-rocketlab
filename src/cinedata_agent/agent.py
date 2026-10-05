"""Agente Text-to-SQL da CineData com PydanticAI: modelos com fallback, ferramentas e controle de cota."""

import asyncio
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

import pydantic_ai
from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import (
    FallbackExceptionGroup,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import UsageLimits

from cinedata_agent import cache, tools
from cinedata_agent.config import Config, carregar_config
from cinedata_agent.db import ErroConsulta, ResultadoConsulta, inicializar_banco
from cinedata_agent.guardrails import verificar_pergunta
from cinedata_agent.prompts import montar_prompt_sistema

logger = logging.getLogger(__name__)

pydantic_ai.BANNER_ENABLED = False  # sem o banner de propaganda do PydanticAI na saída

MAX_REQUISICOES = 6  # chamadas ao modelo por pergunta
MAX_TENTATIVAS_SQL = 3  # novas tentativas de uma ferramenta após erro (autocorreção)

MENSAGEM_COTA = (
    "A cota diária gratuita do OpenRouter acabou. Ela zera às 21h (horário de Brasília); "
    "tente de novo depois desse horário."
)

# Falhas de modelo (que também gastam cota) da pergunta em andamento.
_falhas_modelo: ContextVar[list[str] | None] = ContextVar("_falhas_modelo", default=None)


class QuotaExceededError(Exception):
    """Cota diária do OpenRouter esgotada (429 sem provedor): não adianta tentar outros modelos."""

    def __init__(self, mensagem: str = MENSAGEM_COTA) -> None:
        super().__init__(mensagem)


class ErroAgente(Exception):
    """O agente não conseguiu responder (limite de requisições, de tentativas ou modelos fora do ar)."""


@dataclass
class DepsAgente:
    """Dependências de uma pergunta: registro dos resultados completos de cada run_sql."""

    consultas: list[ResultadoConsulta] = field(default_factory=list)


class RespostaAgente(BaseModel):
    """Resposta do agente para uma pergunta."""

    resposta: str
    sql_executados: list[str]
    colunas: list[str]
    linhas: list[dict[str, Any]]
    modelo: str | None
    requisicoes: int
    tempo_ms: int
    historico: list[ModelMessage]
    cache: bool = False  # veio do cache (0 requisições)
    recusada: bool = False  # recusada pelos guardrails antes de chamar o modelo


# --- Modelos e tratamento de erros ---

def classificar_erro(erro: Exception) -> Literal["trocar", "cota", "outro"]:
    """Classifica o erro do modelo segundo o guia do Rocket Lab.

    - 429 com provedor (upstream) ou 404: modelo lotado ou fora do ar -> "trocar" de modelo.
    - 429 sem provedor: cota diária esgotada -> "cota" (não tentar outros modelos).
    - 5xx com provedor (ex.: 502 "Provider returned error"): falha do provedor -> "trocar".
    """
    if not isinstance(erro, ModelHTTPError):
        return "outro"
    corpo = str(erro.body).lower()
    if erro.status_code == 404:
        return "trocar"
    if erro.status_code == 429:
        return "trocar" if "upstream" in corpo or "provider_name" in corpo else "cota"
    if erro.status_code >= 500 and "provider_name" in corpo:
        return "trocar"
    return "outro"


def _deve_trocar_de_modelo(erro: Exception) -> bool:
    """Critério do FallbackModel: só passa ao próximo modelo se o atual estiver lotado ou fora do ar."""
    tipo = classificar_erro(erro)
    if tipo == "outro":
        return False
    modelo = getattr(erro, "model_name", "?")
    falhas = _falhas_modelo.get()
    if falhas is not None:
        falhas.append(modelo)
    if tipo == "trocar":
        logger.warning("Modelo %s indisponível (%s); passando ao próximo.", modelo, erro)
        return True
    logger.error("Cota diária esgotada (modelo %s): %s", modelo, erro)
    return False


def criar_modelo(config: Config) -> Model:
    """Monta o modelo principal e os de fallback, todos no LLM_BASE_URL com a LLM_API_KEY."""
    # max_retries=0: o SDK da OpenAI repetiria o 429 sozinho no mesmo modelo, gastando cota.
    cliente = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key or "sem-chave", max_retries=0)
    provider = OpenRouterProvider(openai_client=cliente)
    nomes = [config.modelo, *config.modelos_fallback]
    modelos = [OpenRouterModel(nome, provider=provider) for nome in nomes]
    logger.info("Modelos configurados (em ordem): %s", ", ".join(nomes))
    return FallbackModel(*modelos, fallback_on=_deve_trocar_de_modelo)


@lru_cache(maxsize=1)
def _modelo_padrao() -> Model:
    """Modelo do .env, criado uma vez por processo."""
    return criar_modelo(carregar_config())


# --- Agente e ferramentas ---

def _executar(nome: str, funcao: Any, *args: Any, **kwargs: Any) -> Any:
    """Executa uma ferramenta com log; ErroConsulta vira ModelRetry para o modelo se corrigir."""
    inicio = time.perf_counter()
    try:
        resultado = funcao(*args, **kwargs)
    except ErroConsulta as erro:
        logger.warning("Ferramenta %s falhou (devolvido ao modelo): %s", nome, erro)
        raise ModelRetry(str(erro)) from erro
    logger.info("Ferramenta %s executada em %.0f ms.", nome, (time.perf_counter() - inicio) * 1000)
    return resultado


@lru_cache(maxsize=1)
def criar_agente() -> Agent[DepsAgente, str]:
    """Cria o agente (sem modelo fixo: ele é passado em cada execução) com as 4 ferramentas."""
    agente: Agent[DepsAgente, str] = Agent(
        deps_type=DepsAgente,
        instructions=montar_prompt_sistema(),
        name="cinedata",
    )

    @agente.tool(retries=MAX_TENTATIVAS_SQL, description=tools.run_sql.__doc__)
    def run_sql(ctx: RunContext[DepsAgente], sql: str) -> dict[str, Any]:
        logger.info("run_sql:\n%s", sql)
        return _executar("run_sql", tools.run_sql, sql, registro=ctx.deps.consultas)

    @agente.tool_plain(retries=MAX_TENTATIVAS_SQL, description=tools.list_tables.__doc__)
    def list_tables() -> list[dict[str, str]]:
        return _executar("list_tables", tools.list_tables)

    @agente.tool_plain(retries=MAX_TENTATIVAS_SQL, description=tools.describe_table.__doc__)
    def describe_table(table_name: str) -> dict[str, Any]:
        return _executar("describe_table", tools.describe_table, table_name)

    @agente.tool_plain(retries=MAX_TENTATIVAS_SQL, description=tools.sample_values.__doc__)
    def sample_values(table_name: str, column_name: str) -> dict[str, Any]:
        return _executar("sample_values", tools.sample_values, table_name, column_name)

    return agente


# --- Perguntas ---

def _erro_cota(erro: Exception) -> bool:
    """Diz se o erro (ou algum erro dentro do grupo do fallback) é de cota esgotada."""
    if isinstance(erro, FallbackExceptionGroup):
        return any(_erro_cota(e) for e in erro.exceptions)
    return classificar_erro(erro) == "cota"


def _recusar(mensagem: str, historico: list[ModelMessage] | None) -> RespostaAgente:
    """Resposta de recusa dos guardrails: sem SQL e sem gastar cota; o histórico segue igual."""
    return RespostaAgente(
        resposta=mensagem,
        sql_executados=[],
        colunas=[],
        linhas=[],
        modelo=None,
        requisicoes=0,
        tempo_ms=0,
        historico=list(historico or []),
        recusada=True,
    )


def _buscar_no_cache(chave: str, inicio: float) -> RespostaAgente | None:
    """Devolve a resposta guardada no cache (marcada com cache=True e 0 requisições), se houver."""
    guardada = cache.buscar(chave)
    if guardada is None:
        return None
    try:
        resposta = RespostaAgente.model_validate_json(guardada)
    except ValueError as erro:
        logger.warning("Resposta do cache ilegível (%s); chamando o modelo.", erro)
        return None
    tempo_ms = round((time.perf_counter() - inicio) * 1000)
    logger.info("Resposta vinda do cache em %d ms (0 requisições).", tempo_ms)
    return resposta.model_copy(update={"cache": True, "requisicoes": 0, "tempo_ms": tempo_ms})


async def ask_async(
    pergunta: str,
    historico: list[ModelMessage] | None = None,
    modelo: Model | None = None,
    usar_cache: bool = True,
) -> RespostaAgente:
    """Responde uma pergunta (versão assíncrona, para a API). Ver ask()."""
    recusa = verificar_pergunta(pergunta)
    if recusa is not None:
        return _recusar(recusa.mensagem, historico)

    inicio = time.perf_counter()
    modelo = modelo or _modelo_padrao()
    chave = cache.chave_cache(pergunta, modelo.model_name, montar_prompt_sistema())
    com_cache = usar_cache and not historico  # acompanhamento depende do histórico: sem cache
    if com_cache and (guardada := _buscar_no_cache(chave, inicio)) is not None:
        return guardada

    inicializar_banco()
    deps = DepsAgente()
    falhas: list[str] = []
    token = _falhas_modelo.set(falhas)
    logger.info("Pergunta: %s", pergunta)
    try:
        resultado = await criar_agente().run(
            pergunta,
            model=modelo,
            deps=deps,
            message_history=historico,
            usage_limits=UsageLimits(request_limit=MAX_REQUISICOES),
        )
    except UsageLimitExceeded as erro:
        raise ErroAgente(
            f"A pergunta passou do limite de {MAX_REQUISICOES} chamadas ao modelo. "
            "Tente reformular de forma mais direta."
        ) from erro
    except UnexpectedModelBehavior as erro:
        raise ErroAgente(
            f"O modelo não conseguiu montar uma consulta válida ({erro}). Tente reformular a pergunta."
        ) from erro
    except Exception as erro:
        if _erro_cota(erro):
            raise QuotaExceededError() from erro
        if isinstance(erro, FallbackExceptionGroup):
            raise ErroAgente(
                "Todos os modelos configurados estão indisponíveis agora. Tente de novo em alguns minutos."
            ) from erro
        raise
    finally:
        _falhas_modelo.reset(token)

    tempo_ms = round((time.perf_counter() - inicio) * 1000)
    ultima = deps.consultas[-1] if deps.consultas else None
    resposta = RespostaAgente(
        resposta=resultado.output,
        sql_executados=[c.sql_executado for c in deps.consultas],
        colunas=ultima.colunas if ultima else [],
        linhas=ultima.linhas if ultima else [],
        modelo=resultado.response.model_name,
        requisicoes=resultado.usage.requests + len(falhas),
        tempo_ms=tempo_ms,
        historico=resultado.all_messages(),
    )
    logger.info(
        "Resposta pelo modelo %s: %d requisições (%d falhas de modelo), %d SQL, %d ms.",
        resposta.modelo, resposta.requisicoes, len(falhas), len(resposta.sql_executados), tempo_ms,
    )
    if com_cache:  # só respostas bem-sucedidas chegam aqui; erros saíram por exceção
        cache.salvar(chave, pergunta, modelo.model_name, resposta.model_dump_json())
    return resposta


def ask(
    pergunta: str,
    historico: list[ModelMessage] | None = None,
    modelo: Model | None = None,
    usar_cache: bool = True,
) -> RespostaAgente:
    """Responde uma pergunta em português consultando o banco.

    Antes do modelo, aplica os guardrails (recusa sem gastar cota) e consulta o cache (desligado
    com usar_cache=False ou quando há histórico). Passe o `historico` da resposta anterior para
    manter a memória da conversa. Levanta QuotaExceededError se a cota diária acabou e
    ErroAgente se não conseguir responder.
    """
    return _event_loop().run_until_complete(ask_async(pergunta, historico, modelo, usar_cache))


@lru_cache(maxsize=1)
def _event_loop() -> asyncio.AbstractEventLoop:
    """Loop único para o ask() síncrono: o cliente HTTP do modelo fica preso ao loop em que nasceu."""
    return asyncio.new_event_loop()
