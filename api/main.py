"""API FastAPI do CineData Agent: perguntas (/ask), saúde (/health), esquema (/schema) e cota (/quota).

Para subir: uvicorn api.main:app --reload (documentação interativa em /docs).
"""

import logging
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from cinedata_agent import db
from cinedata_agent.agent import (
    MENSAGEM_COTA,
    ErroAgente,
    QuotaExceededError,
    ask_async,
)
from cinedata_agent.config import carregar_config
from cinedata_agent.db import EstatisticasBanco, inicializar_banco
from cinedata_agent.guardrails import MAX_CARACTERES
from cinedata_agent.quota import ErroCota, consultar_cota
from cinedata_agent.schema import carregar_schema

logger = logging.getLogger(__name__)

MAX_SESSOES = 200  # conversas guardadas em memória; a mais antiga sai primeiro
MENSAGEM_ERRO_INTERNO = "Erro interno ao processar a pergunta. Tente de novo mais tarde."


# --- Memória das conversas ---


class MemoriaConversas:
    """Histórico de mensagens por session_id, em memória, com limite de sessões."""

    def __init__(self, maximo: int = MAX_SESSOES) -> None:
        self.maximo = maximo
        self._sessoes: OrderedDict[str, list[ModelMessage]] = OrderedDict()

    def buscar(self, session_id: str) -> list[ModelMessage] | None:
        """Histórico da sessão, ou None se ela ainda não existe."""
        historico = self._sessoes.get(session_id)
        if historico is not None:
            self._sessoes.move_to_end(session_id)
        return historico

    def guardar(self, session_id: str, historico: list[ModelMessage]) -> None:
        """Guarda o histórico da sessão, descartando a sessão mais antiga se passar do limite."""
        self._sessoes[session_id] = historico
        self._sessoes.move_to_end(session_id)
        while len(self._sessoes) > self.maximo:
            self._sessoes.popitem(last=False)


# --- Modelos de entrada e saída ---


class PerguntaEntrada(BaseModel):
    """Pergunta em português sobre o catálogo de filmes."""

    pergunta: str = Field(
        min_length=1,
        max_length=MAX_CARACTERES,
        description="Pergunta em linguagem natural.",
        examples=["Quais são os 10 filmes com maior receita em R$?"],
    )
    session_id: str | None = Field(
        default=None,
        max_length=100,
        description="Identificador da conversa. Com ele, a API lembra as perguntas anteriores.",
        examples=["conversa-1"],
    )
    usar_cache: bool = Field(
        default=True,
        description="Usa a resposta guardada no cache, se houver (0 requisições). False sempre chama o modelo.",
    )


class RespostaSaida(BaseModel):
    """Resposta do agente com o SQL e o resultado da última consulta."""

    resposta: str = Field(description="Resposta em português.", examples=["O filme com maior receita é..."])
    sql: list[str] = Field(
        description="Consultas SQL executadas, na ordem; a última gerou colunas e linhas.",
        examples=[["SELECT titulo, receita_brl FROM ... ORDER BY receita_brl DESC LIMIT 10"]],
    )
    colunas: list[str] = Field(description="Colunas do resultado.", examples=[["titulo", "receita_brl"]])
    linhas: list[dict[str, Any]] = Field(
        description="Linhas do resultado.",
        examples=[[{"titulo": "Avatar: The Way Of Water (2022)", "receita_brl": 1.2e10}]],
    )
    modelo: str | None = Field(
        description="Modelo que respondeu (None em recusas).", examples=["nvidia/nemotron-3.5-lightning:free"]
    )
    requisicoes: int = Field(description="Requisições gastas no OpenRouter (0 em cache e recusas).", examples=[2])
    tempo_ms: int = Field(description="Tempo de resposta em milissegundos.", examples=[3500])
    cache: bool = Field(description="True se a resposta veio do cache.", examples=[False])
    recusada: bool = Field(description="True se os guardrails recusaram a pergunta.", examples=[False])
    session_id: str | None = Field(description="Conversa usada (a mesma enviada).", examples=["conversa-1"])


class ErroSaida(BaseModel):
    """Mensagem de erro."""

    detail: str = Field(examples=[MENSAGEM_COTA])


class SaudeSaida(BaseModel):
    """Situação da API (não chama o modelo)."""

    status: Literal["ok", "erro"] = Field(examples=["ok"])
    banco: bool = Field(description="O banco em memória responde.", examples=[True])
    chave_configurada: bool = Field(description="LLM_API_KEY está preenchida no .env.", examples=[True])
    estatisticas_banco: EstatisticasBanco | None = Field(description="Custo da carga do banco em memória.")


class ColunaSchema(BaseModel):
    """Coluna do dicionário de dados."""

    nome: str = Field(examples=["receita_usd"])
    tipo: str = Field(examples=["NUMERIC(18,2)"])
    descricao: str = Field(examples=["Receita; NULL = não informada (nunca 0)."])


class TabelaSchema(BaseModel):
    """Tabela do dicionário de dados."""

    nome: str = Field(examples=["fact_movies_performance"])
    descricao: str = Field(examples=["Métricas financeiras, de popularidade e de notas."])
    chave_primaria: str = Field(examples=["sk_movie_id"])
    auxiliar: bool = Field(description="Criada pela API ao subir (só em memória, não existe no arquivo).")
    colunas: list[ColunaSchema]


class CotaSaida(BaseModel):
    """Cota diária de modelos gratuitos do OpenRouter."""

    used: int | None = Field(default=None, description="Requisições usadas hoje.", examples=[9])
    limit: int | None = Field(default=None, description="Limite diário.", examples=[50])
    remaining: int | None = Field(default=None, description="Requisições restantes hoje.", examples=[41])


# --- Aplicação ---


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Carrega o banco em memória ao subir, para a primeira pergunta não esperar a carga."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    inicializar_banco()
    app.state.memoria = MemoriaConversas()
    yield


app = FastAPI(
    title="CineData Agent",
    description=(
        "Perguntas em português sobre o catálogo de filmes da CineData, respondidas com SQL somente "
        "leitura. Atenção: cada pergunta nova gasta cota do OpenRouter (50 por dia na conta gratuita); "
        "respostas do cache e recusas não gastam."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.post(
    "/ask",
    response_model=RespostaSaida,
    summary="Faz uma pergunta ao agente",
    responses={
        429: {"model": ErroSaida, "description": "Cota diária do OpenRouter esgotada."},
        503: {"model": ErroSaida, "description": "O agente não conseguiu responder (modelos fora do ar, limites)."},
        500: {"model": ErroSaida, "description": "Erro interno (sem detalhes)."},
    },
)
async def ask(entrada: PerguntaEntrada, request: Request) -> RespostaSaida:
    """Responde a pergunta. Recusas dos guardrails voltam com status 200 e `recusada=true`."""
    memoria: MemoriaConversas = request.app.state.memoria
    historico = memoria.buscar(entrada.session_id) if entrada.session_id else None
    try:
        resposta = await ask_async(entrada.pergunta, historico, usar_cache=entrada.usar_cache)
    except QuotaExceededError as erro:
        raise HTTPException(status_code=429, detail=str(erro)) from erro
    except ErroAgente as erro:
        raise HTTPException(status_code=503, detail=str(erro)) from erro
    except Exception as erro:
        logger.exception("Erro interno ao responder a pergunta.")
        raise HTTPException(status_code=500, detail=MENSAGEM_ERRO_INTERNO) from erro

    if entrada.session_id:
        memoria.guardar(entrada.session_id, resposta.historico)
    return RespostaSaida(
        resposta=resposta.resposta,
        sql=resposta.sql_executados,
        colunas=resposta.colunas,
        linhas=resposta.linhas,
        modelo=resposta.modelo,
        requisicoes=resposta.requisicoes,
        tempo_ms=resposta.tempo_ms,
        cache=resposta.cache,
        recusada=resposta.recusada,
        session_id=entrada.session_id,
    )


@app.get(
    "/health",
    response_model=SaudeSaida,
    summary="Confere o banco e a chave (sem chamar o modelo)",
    responses={503: {"model": SaudeSaida, "description": "O banco não respondeu."}},
)
def health(response: Response) -> SaudeSaida:
    """Diz se o banco em memória responde e se a chave do OpenRouter está configurada."""
    try:
        db.obter_conexao().execute("SELECT 1").fetchone()
        banco_ok = True
    except Exception:
        logger.exception("Banco indisponível no /health.")
        banco_ok = False
    saude = SaudeSaida(
        status="ok" if banco_ok else "erro",
        banco=banco_ok,
        chave_configurada=bool(carregar_config().api_key),
        estatisticas_banco=db.estatisticas(),
    )
    if not banco_ok:
        response.status_code = 503
    return saude


@app.get("/schema", response_model=list[TabelaSchema], summary="Tabelas e descrições do dicionário de dados")
def schema() -> list[TabelaSchema]:
    """Devolve as tabelas de docs/schema_docs.yaml com as colunas e descrições."""
    dados = carregar_schema()
    grupos = [(dados["tabelas"], False), (dados.get("tabelas_auxiliares", {}), True)]
    return [
        TabelaSchema(
            nome=nome,
            descricao=" ".join(str(info["descricao"]).split()),
            chave_primaria=str(info["chave_primaria"]),
            auxiliar=auxiliar,
            colunas=[
                ColunaSchema(nome=coluna, tipo=str(col.get("tipo", "")), descricao=str(col["descricao"]))
                for coluna, col in info["colunas"].items()
            ],
        )
        for tabelas, auxiliar in grupos
        for nome, info in tabelas.items()
    ]


@app.get(
    "/quota",
    response_model=CotaSaida,
    summary="Cota restante do OpenRouter (não gasta cota)",
    responses={503: {"model": ErroSaida, "description": "Não foi possível consultar a cota."}},
)
def quota() -> CotaSaida:
    """Consulta a cota diária de modelos gratuitos do OpenRouter."""
    try:
        return CotaSaida.model_validate(consultar_cota())
    except ErroCota as erro:
        raise HTTPException(status_code=503, detail=str(erro)) from erro
