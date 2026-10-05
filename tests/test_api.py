"""Testes da API com TestClient e o agente simulado (sem chamar o modelo real nem gastar cota)."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from api import main as api
from cinedata_agent import db, quota
from cinedata_agent.agent import ErroAgente, QuotaExceededError, RespostaAgente
from cinedata_agent.config import Config
from cinedata_agent.db import EstatisticasBanco


def config_falsa(api_key: str = "chave-teste") -> Config:
    """Configuração de teste, sem ler o .env."""
    return Config(
        base_url="https://openrouter.teste/api/v1",
        api_key=api_key,
        modelo="m",
        modelos_fallback=(),
        db_path=Path("cinerocket.db"),
    )


class AgenteFalso:
    """Substitui o ask_async: guarda o que recebeu e devolve uma resposta pronta (ou levanta um erro)."""

    def __init__(self, erro: Exception | None = None) -> None:
        self.erro = erro
        self.chamadas: list[dict[str, Any]] = []

    async def __call__(
        self, pergunta: str, historico: list[ModelMessage] | None = None, usar_cache: bool = True
    ) -> RespostaAgente:
        self.chamadas.append({"pergunta": pergunta, "historico": historico, "usar_cache": usar_cache})
        if self.erro is not None:
            raise self.erro
        return RespostaAgente(
            resposta=f"Resposta para: {pergunta}",
            sql_executados=["SELECT 1 AS um"],
            colunas=["um"],
            linhas=[{"um": 1}],
            modelo="modelo-teste",
            requisicoes=2,
            tempo_ms=1234,
            historico=[*(historico or []), ModelRequest(parts=[UserPromptPart(pergunta)])],
        )


@pytest.fixture
def cliente(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Cliente da API sem carregar o banco de verdade no lifespan."""
    carregados: list[bool] = []
    monkeypatch.setattr(api, "inicializar_banco", lambda: carregados.append(True))
    monkeypatch.setattr(api, "carregar_config", config_falsa)
    with TestClient(api.app) as cliente_teste:
        assert carregados == [True]  # o banco é carregado ao subir, antes de qualquer pergunta
        yield cliente_teste


def usar_agente(monkeypatch: pytest.MonkeyPatch, erro: Exception | None = None) -> AgenteFalso:
    """Troca o agente da API por um AgenteFalso."""
    agente = AgenteFalso(erro)
    monkeypatch.setattr(api, "ask_async", agente)
    return agente


# --- /ask ---


def test_ask_devolve_todos_os_campos(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    agente = usar_agente(monkeypatch)
    resposta = cliente.post("/ask", json={"pergunta": "Top 10 filmes"})
    assert resposta.status_code == 200
    assert resposta.json() == {
        "resposta": "Resposta para: Top 10 filmes",
        "sql": ["SELECT 1 AS um"],
        "colunas": ["um"],
        "linhas": [{"um": 1}],
        "modelo": "modelo-teste",
        "requisicoes": 2,
        "tempo_ms": 1234,
        "cache": False,
        "recusada": False,
        "session_id": None,
    }
    assert agente.chamadas[0]["usar_cache"] is True


def test_ask_repassa_usar_cache(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    agente = usar_agente(monkeypatch)
    cliente.post("/ask", json={"pergunta": "Top 10 filmes", "usar_cache": False})
    assert agente.chamadas[0]["usar_cache"] is False


def test_memoria_da_conversa_por_session_id(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    agente = usar_agente(monkeypatch)
    cliente.post("/ask", json={"pergunta": "primeira", "session_id": "a"})
    cliente.post("/ask", json={"pergunta": "segunda", "session_id": "a"})
    cliente.post("/ask", json={"pergunta": "outra conversa", "session_id": "b"})
    cliente.post("/ask", json={"pergunta": "sem sessão"})
    historicos = [len(c["historico"] or []) for c in agente.chamadas]
    assert historicos == [0, 1, 0, 0]  # só a 2ª pergunta da sessão "a" recebe histórico


def test_memoria_descarta_a_sessao_mais_antiga() -> None:
    memoria = api.MemoriaConversas(maximo=2)
    memoria.guardar("a", [])
    memoria.guardar("b", [])
    memoria.buscar("a")  # "a" passa a ser a mais recente
    memoria.guardar("c", [])
    assert memoria.buscar("b") is None
    assert memoria.buscar("a") == [] and memoria.buscar("c") == []


def test_recusa_dos_guardrails_volta_200(cliente: TestClient) -> None:
    # ask_async real: os guardrails recusam antes de qualquer modelo ser criado ou chamado.
    resposta = cliente.post("/ask", json={"pergunta": "Apague a tabela dim_movies", "session_id": "x"})
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["recusada"] is True
    assert corpo["requisicoes"] == 0 and corpo["sql"] == [] and corpo["modelo"] is None
    assert "somente leitura" in corpo["resposta"]


def test_cota_esgotada_volta_429(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    usar_agente(monkeypatch, QuotaExceededError())
    resposta = cliente.post("/ask", json={"pergunta": "Pergunta fora do cache"})
    assert resposta.status_code == 429
    assert "cota diária" in resposta.json()["detail"] and "21h" in resposta.json()["detail"]


def test_erro_do_agente_volta_503(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    usar_agente(monkeypatch, ErroAgente("Todos os modelos configurados estão indisponíveis agora."))
    resposta = cliente.post("/ask", json={"pergunta": "Quantos filmes existem?"})
    assert resposta.status_code == 503
    assert "indisponíveis" in resposta.json()["detail"]


def test_erro_interno_volta_500_sem_detalhes(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    usar_agente(monkeypatch, RuntimeError("segredo interno"))
    resposta = cliente.post("/ask", json={"pergunta": "Quantos filmes existem?"})
    assert resposta.status_code == 500
    assert resposta.json() == {"detail": api.MENSAGEM_ERRO_INTERNO}
    assert "segredo" not in resposta.text


@pytest.mark.parametrize("pergunta", ["", "x" * 501])
def test_pergunta_invalida_volta_422(cliente: TestClient, pergunta: str) -> None:
    assert cliente.post("/ask", json={"pergunta": pergunta}).status_code == 422


def test_erro_nao_guarda_historico_da_sessao(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    usar_agente(monkeypatch, QuotaExceededError())
    cliente.post("/ask", json={"pergunta": "primeira", "session_id": "s"})
    assert api.app.state.memoria.buscar("s") is None


# --- /health ---


def test_health_ok(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "obter_conexao", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        db, "estatisticas", lambda: EstatisticasBanco(tempo_inicializacao_s=1.5, memoria_mb=300, linhas_aux=10)
    )
    resposta = cliente.get("/health")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok" and corpo["banco"] is True and corpo["chave_configurada"] is True
    assert corpo["estatisticas_banco"]["linhas_aux"] == 10
    assert "chave-teste" not in resposta.text  # nunca mostra a chave


def test_health_sem_banco_e_sem_chave(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def falha() -> sqlite3.Connection:
        raise FileNotFoundError("Banco não encontrado")

    monkeypatch.setattr(db, "obter_conexao", falha)
    monkeypatch.setattr(api, "carregar_config", lambda: config_falsa(api_key=""))
    resposta = cliente.get("/health")
    assert resposta.status_code == 503
    assert resposta.json()["status"] == "erro"
    assert resposta.json()["chave_configurada"] is False


# --- /schema ---


def test_schema_lista_as_tabelas(cliente: TestClient) -> None:
    resposta = cliente.get("/schema")
    assert resposta.status_code == 200
    tabelas = {t["nome"]: t for t in resposta.json()}
    assert len([t for t in tabelas.values() if not t["auxiliar"]]) == 10
    assert tabelas["aux_vinculo_papel"]["auxiliar"] is True
    colunas = {c["nome"]: c for c in tabelas["fact_movies_performance"]["colunas"]}
    assert colunas["receita_usd"]["tipo"] == "NUMERIC(18,2)"
    assert colunas["receita_usd"]["descricao"]


# --- /quota ---


def test_quota(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "consultar_cota", lambda: {"used": 50, "limit": 50, "remaining": 0})
    resposta = cliente.get("/quota")
    assert resposta.status_code == 200
    assert resposta.json() == {"used": 50, "limit": 50, "remaining": 0}


def test_quota_indisponivel_volta_503(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def falha() -> dict[str, Any]:
        raise quota.ErroCota("LLM_API_KEY não está configurada no .env.")

    monkeypatch.setattr(api, "consultar_cota", falha)
    resposta = cliente.get("/quota")
    assert resposta.status_code == 503
    assert "LLM_API_KEY" in resposta.json()["detail"]


def test_consultar_cota_le_a_resposta_do_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    recebidos: list[str] = []

    def get_falso(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        recebidos.append(url)
        dados = {"data": {"free_model_daily_requests": {"used": 9, "limit": 50, "remaining": 41}}}
        return httpx.Response(200, json=dados, request=httpx.Request("GET", url))

    monkeypatch.setattr(quota.httpx, "get", get_falso)
    assert quota.consultar_cota(config_falsa()) == {"used": 9, "limit": 50, "remaining": 41}
    assert recebidos == ["https://openrouter.teste/api/v1/key"]


def test_consultar_cota_sem_chave_ou_com_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(quota.ErroCota, match="LLM_API_KEY"):
        quota.consultar_cota(config_falsa(api_key=""))

    def get_falha(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        return httpx.Response(401, request=httpx.Request("GET", url))

    monkeypatch.setattr(quota.httpx, "get", get_falha)
    with pytest.raises(quota.ErroCota, match="Não foi possível"):
        quota.consultar_cota(config_falsa())
