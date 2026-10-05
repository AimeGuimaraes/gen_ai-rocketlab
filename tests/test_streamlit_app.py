"""Testes da interface Streamlit com AppTest; o agente e a cota são substituídos (sem modelo real)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from cinedata_agent import agent, db, quota

CAMINHO_APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


def _resposta(**campos: Any) -> agent.RespostaAgente:
    """Resposta falsa do agente: nota média por ano (gera tabela e gráfico de linha)."""
    padrao: dict[str, Any] = {
        "resposta": "A nota média IMDb por ano vai de 6,1 a 6,8.",
        "sql_executados": ["SELECT ano_lancamento, AVG(nota_imdb) AS nota_media_imdb FROM dim_movies GROUP BY 1"],
        "colunas": ["ano_lancamento", "nota_media_imdb"],
        "linhas": [{"ano_lancamento": 2016 + i, "nota_media_imdb": 6.1 + i / 10} for i in range(5)],
        "modelo": "modelo-falso:free",
        "requisicoes": 2,
        "tempo_ms": 1500,
        "historico": [],
    }
    return agent.RespostaAgente.model_construct(**(padrao | campos))  # sem validar o histórico falso


@pytest.fixture(autouse=True)
def sem_servicos(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, Any]]]:
    """Troca o agente, a cota e a carga do banco por versões falsas; devolve as chamadas ao ask()."""
    chamadas: list[tuple[str, Any]] = []

    def ask_falso(pergunta: str, historico: Any = None) -> agent.RespostaAgente:
        chamadas.append((pergunta, historico))
        return _resposta(historico=[*(historico or []), pergunta])

    monkeypatch.setattr(agent, "ask", ask_falso)
    monkeypatch.setattr(quota, "consultar_cota", lambda: {"used": 4, "limit": 50, "remaining": 46})
    monkeypatch.setattr(db, "inicializar_banco", lambda: None)
    st.cache_data.clear()
    yield chamadas
    st.cache_data.clear()


def _app() -> AppTest:
    """Abre o app e roda a primeira vez."""
    return AppTest.from_file(CAMINHO_APP, default_timeout=30).run()


def test_tela_inicial_mostra_cota_modelo_e_exemplos() -> None:
    app = _app()
    assert not app.exception
    assert app.sidebar.metric[0].value == "46 de 50"
    rotulos = [b.label for b in app.sidebar.button]
    assert "🔄 Nova conversa" in rotulos
    assert len(rotulos) == 6  # nova conversa + 5 exemplos


def test_pergunta_mostra_texto_tabela_grafico_e_sql(sem_servicos: list[tuple[str, Any]]) -> None:
    app = _app()
    app.chat_input[0].set_value("Qual é a nota média IMDb por ano?").run()
    assert not app.exception
    assert len(app.chat_message) == 2
    assert "6,1 a 6,8" in app.chat_message[1].markdown[0].value
    assert len(app.dataframe) == 1
    assert len(app.get("plotly_chart")) == 1
    assert app.expander[0].label == "SQL executado"
    assert "AVG(nota_imdb)" in app.code[0].value
    assert sem_servicos == [("Qual é a nota média IMDb por ano?", None)]


def test_conversa_passa_o_historico_e_nova_conversa_limpa(sem_servicos: list[tuple[str, Any]]) -> None:
    app = _app()
    app.chat_input[0].set_value("Primeira").run()
    app.chat_input[0].set_value("E a segunda?").run()
    assert sem_servicos[1] == ("E a segunda?", ["Primeira"])  # memória da conversa
    assert len(app.chat_message) == 4

    app.sidebar.button[0].click().run()
    assert len(app.chat_message) == 0
    app.chat_input[0].set_value("Outra").run()
    assert sem_servicos[2] == ("Outra", None)


def test_botao_de_exemplo_faz_a_pergunta(sem_servicos: list[tuple[str, Any]]) -> None:
    app = _app()
    app.sidebar.button[1].click().run()
    assert sem_servicos[0][0] == app.sidebar.button[1].label


def test_cota_acabou_mostra_mensagem_amigavel(monkeypatch: pytest.MonkeyPatch) -> None:
    def ask_sem_cota(pergunta: str, historico: Any = None) -> agent.RespostaAgente:
        raise agent.QuotaExceededError()

    monkeypatch.setattr(agent, "ask", ask_sem_cota)
    app = _app()
    app.chat_input[0].set_value("Quais são os 5 filmes mais populares?").run()
    assert not app.exception
    assert "cota diária gratuita" in app.warning[0].value
    assert "cache" in app.warning[0].value


def test_erro_do_agente_vira_mensagem(monkeypatch: pytest.MonkeyPatch) -> None:
    def ask_com_erro(pergunta: str, historico: Any = None) -> agent.RespostaAgente:
        raise agent.ErroAgente("Todos os modelos configurados estão indisponíveis agora.")

    monkeypatch.setattr(agent, "ask", ask_com_erro)
    app = _app()
    app.chat_input[0].set_value("Pergunta").run()
    assert "indisponíveis" in app.error[0].value


def test_pergunta_pela_url(sem_servicos: list[tuple[str, Any]]) -> None:
    app = AppTest.from_file(CAMINHO_APP, default_timeout=30)
    app.query_params["pergunta"] = "Qual é a quantidade de filmes por gênero?"
    app.run()
    assert sem_servicos == [("Qual é a quantidade de filmes por gênero?", None)]
    app.run()  # não repete a pergunta da URL
    assert len(sem_servicos) == 1
