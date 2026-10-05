"""Testes do cache de respostas (sem chamar o modelo real; cache em pasta temporária)."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cinedata_agent import agent as modulo_agente
from cinedata_agent import cache
from cinedata_agent.agent import ErroAgente, QuotaExceededError, ask

PERGUNTA = "Quantos filmes existem por gênero?"


@pytest.fixture(autouse=True)
def sem_banco(monkeypatch: pytest.MonkeyPatch) -> None:
    """Estes testes não executam SQL: evita carregar o banco."""
    monkeypatch.setattr(modulo_agente, "inicializar_banco", lambda: None)


def modelo_contador(*textos: str, chamadas: list[str]) -> FunctionModel:
    """Modelo falso que responde os textos na ordem e registra cada chamada."""
    fila: Iterator[str] = iter(textos)

    def funcao(mensagens: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        chamadas.append("chamou")
        return ModelResponse(parts=[TextPart(next(fila))])

    return FunctionModel(funcao, model_name="modelo-teste")


def linhas_no_cache() -> int:
    """Quantas respostas há no cache temporário."""
    if not cache.CAMINHO_CACHE.exists():
        return 0
    conexao = sqlite3.connect(cache.CAMINHO_CACHE)
    try:
        return conexao.execute("SELECT COUNT(*) FROM respostas").fetchone()[0]
    finally:
        conexao.close()


# --- Chave ---


def test_chave_ignora_caixa_acentos_e_espacos() -> None:
    base = cache.chave_cache(PERGUNTA, "m", "prompt")
    assert cache.chave_cache("  quantos FILMES   existem por genero? ", "m", "prompt") == base


def test_chave_muda_com_modelo_prompt_e_pergunta() -> None:
    base = cache.chave_cache(PERGUNTA, "m", "prompt")
    assert cache.chave_cache(PERGUNTA, "outro-modelo", "prompt") != base
    assert cache.chave_cache(PERGUNTA, "m", "prompt ajustado") != base
    assert cache.chave_cache("Quantos filmes existem?", "m", "prompt") != base


# --- Armazenamento ---


def test_salvar_e_buscar(cache_temporario: Path) -> None:
    assert cache.buscar("k") is None
    cache.salvar("k", PERGUNTA, "m", '{"x": 1}')
    assert cache.buscar("k") == '{"x": 1}'
    assert cache_temporario.exists()
    assert cache_temporario.name == "cache.db" and cache_temporario.parent.name == ".cache"


def test_cache_corrompido_nao_quebra_o_agente(cache_temporario: Path) -> None:
    cache_temporario.parent.mkdir(parents=True)
    cache_temporario.write_bytes(b"isto nao e um sqlite" * 100)
    chamadas: list[str] = []
    resposta = ask(PERGUNTA, modelo=modelo_contador("ok", chamadas=chamadas))
    assert resposta.resposta == "ok"
    assert resposta.cache is False


# --- Integração com o ask() ---


def test_segunda_pergunta_vem_do_cache() -> None:
    chamadas: list[str] = []
    modelo = modelo_contador("São 19 gêneros.", chamadas=chamadas)  # só uma resposta disponível
    primeira = ask(PERGUNTA, modelo=modelo)
    segunda = ask("  quantos filmes existem por GENERO? ", modelo=modelo)
    assert chamadas == ["chamou"]
    assert primeira.cache is False and primeira.requisicoes == 1
    assert segunda.cache is True and segunda.requisicoes == 0
    assert segunda.resposta == "São 19 gêneros."
    assert segunda.modelo == "modelo-teste"
    assert len(segunda.historico) == len(primeira.historico)  # dá para continuar a conversa


def test_sem_cache_quando_ha_historico() -> None:
    chamadas: list[str] = []
    modelo = modelo_contador("primeira", "acompanhamento", "de novo", chamadas=chamadas)
    primeira = ask("Qual o filme mais popular?", modelo=modelo)
    ask(PERGUNTA, historico=primeira.historico, modelo=modelo)
    assert linhas_no_cache() == 1  # só a primeira; o acompanhamento não foi gravado
    resposta = ask(PERGUNTA, historico=primeira.historico, modelo=modelo)
    assert resposta.cache is False
    assert len(chamadas) == 3


def test_usar_cache_false_nao_le_nem_grava() -> None:
    chamadas: list[str] = []
    modelo = modelo_contador("um", "dois", "três", chamadas=chamadas)
    ask(PERGUNTA, modelo=modelo, usar_cache=False)
    assert linhas_no_cache() == 0  # não gravou
    ask(PERGUNTA, modelo=modelo)  # agora grava
    resposta = ask(PERGUNTA, modelo=modelo, usar_cache=False)  # não lê
    assert resposta.cache is False and resposta.resposta == "três"
    assert len(chamadas) == 3


def test_erros_nao_entram_no_cache() -> None:
    def sem_cota(mensagens: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(429, "m", {"message": "Rate limit exceeded: free-models-per-day."})

    def sempre_ferramenta(mensagens: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("list_tables", {})])

    with pytest.raises(QuotaExceededError):
        ask(PERGUNTA, modelo=FunctionModel(sem_cota, model_name="modelo-teste"))
    with pytest.raises(ErroAgente):
        ask(PERGUNTA, modelo=FunctionModel(sempre_ferramenta, model_name="modelo-teste"))
    assert linhas_no_cache() == 0
