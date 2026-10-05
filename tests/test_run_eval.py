"""Testes do eval/run_eval.py: comparador com resultados simulados e fluxo com modelo falso (sem gastar cota)."""

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from cinedata_agent.agent import QuotaExceededError, RespostaAgente
from cinedata_agent.config import RAIZ_PROJETO

sys.path.insert(0, str(RAIZ_PROJETO / "eval"))
import run_eval

CAMINHO_DB = RAIZ_PROJETO / "cinerocket.db"
precisa_db = pytest.mark.skipif(not CAMINHO_DB.exists(), reason="banco cinerocket.db ausente")

ESPERADO_FILMES = {
    "colunas": ["titulo", "ano_lancamento", "id_filme", "receita_usd"],
    "linhas": [
        ["Filme A", 2019, "10", 1000.0],
        ["Filme B", 2020, "20", 500.5],
        ["Filme C", 2021, "30", 250.25],
    ],
}
ITEM_EXATO = {"id": "q90", "tipo_checagem": "resultado_exato", "chave": "titulo"}
ITEM_TOP = {"id": "q91", "tipo_checagem": "top_n_contem", "chave": "titulo", "top_n": 2}
ITEM_RECUSA = {"id": "q92", "tipo_checagem": "recusa"}
SQL = ["SELECT 1"]


def resultado(colunas: list[str], linhas: list[list[Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Resultado simulado do agente no formato do run_query (colunas + linhas em dicionário)."""
    return colunas, [dict(zip(colunas, linha, strict=True)) for linha in linhas]


def comparar(item: dict[str, Any], colunas: list[str], linhas: list[list[Any]], **kwargs: Any) -> tuple[bool, str]:
    """Atalho: compara um resultado simulado com ESPERADO_FILMES."""
    cols, dicts = resultado(colunas, linhas)
    return run_eval.comparar(item, ESPERADO_FILMES, cols, dicts, kwargs.pop("sqls", SQL), **kwargs)


# --- resultado_exato ---

def test_exato_com_colunas_renomeadas_e_em_outra_ordem() -> None:
    acertou, detalhe = comparar(
        ITEM_EXATO,
        ["receita", "filme"],
        [[250.25, "Filme C"], [1000.0, "Filme A"], [500.5, "Filme B"]],
    )
    assert acertou, detalhe
    assert "titulo→filme" in detalhe and "receita_usd→receita" in detalhe


def test_exato_aceita_titulo_com_ano_e_sem_acento_ou_caixa() -> None:
    esperado = {"colunas": ["titulo", "receita_usd"], "linhas": [["Pokémon", 10.0]]}
    cols, linhas = resultado(["filme", "receita_usd"], [["POKEMON (2019)", 10.0]])
    assert run_eval.comparar(ITEM_EXATO, esperado, cols, linhas, SQL)[0]


@pytest.mark.parametrize(("delta", "esperado"), [(0.005, True), (0.01, True), (0.02, False)])
def test_exato_tolerancia_numerica(delta: float, esperado: bool) -> None:
    linhas = [["Filme A", 1000.0 + delta], ["Filme B", 500.5], ["Filme C", 250.25]]
    assert comparar(ITEM_EXATO, ["titulo", "receita_usd"], linhas)[0] is esperado


def test_exato_falha_com_linha_faltando_ou_sobrando() -> None:
    acertou, detalhe = comparar(ITEM_EXATO, ["titulo", "receita_usd"], [["Filme A", 1000.0], ["Filme B", 500.5]])
    assert not acertou and "faltam Filme C" in detalhe
    linhas = [["Filme A", 1000.0], ["Filme B", 500.5], ["Filme C", 250.25], ["Filme D", 1.0]]
    acertou, detalhe = comparar(ITEM_EXATO, ["titulo", "receita_usd"], linhas)
    assert not acertou and "sobram Filme D" in detalhe


def test_exato_falha_se_valores_trocados_entre_linhas() -> None:
    linhas = [["Filme A", 500.5], ["Filme B", 1000.0], ["Filme C", 250.25]]
    acertou, detalhe = comparar(ITEM_EXATO, ["titulo", "receita_usd"], linhas)
    assert not acertou and "receita_usd" in detalhe


def test_exato_colunas_opcionais_ausentes_nao_reprovam_mas_metricas_sim() -> None:
    linhas = [["Filme A", 1000.0], ["Filme B", 500.5], ["Filme C", 250.25]]
    assert comparar(ITEM_EXATO, ["titulo", "receita_usd"], linhas)[0]  # sem id_filme e ano
    acertou, detalhe = comparar(ITEM_EXATO, ["titulo"], [["Filme A"], ["Filme B"], ["Filme C"]])
    assert not acertou and "receita_usd" in detalhe


def test_exato_coluna_opcional_presente_e_errada_nao_reprova() -> None:
    linhas = [["Filme A", 1999, 1000.0], ["Filme B", 2020, 500.5], ["Filme C", 2021, 250.25]]
    assert comparar(ITEM_EXATO, ["titulo", "ano", "receita_usd"], linhas)[0]


def test_titulos_repetidos_alinham_pelos_valores() -> None:
    esperado = {"colunas": ["titulo", "receita_usd"], "linhas": [["Igual", 10.0], ["Igual", 20.0]]}
    cols, linhas = resultado(["titulo", "receita_usd"], [["Igual", 20.0], ["Igual", 10.0]])
    assert run_eval.comparar(ITEM_EXATO, esperado, cols, linhas, SQL)[0]


def test_chave_numerica_ano() -> None:
    item = {"id": "q06", "tipo_checagem": "resultado_exato", "chave": "ano_lancamento"}
    esperado = {"colunas": ["ano_lancamento", "nota_media_imdb"], "linhas": [[2019, 6.5], [2020, 6.25]]}
    cols, linhas = resultado(["ano", "media"], [[2020.0, 6.251], [2019.0, 6.5]])
    assert run_eval.comparar(item, esperado, cols, linhas, SQL)[0]


SQL_Q04 = """SELECT m.titulo, f.popularidade, f.qtd_tmdb
FROM fact_movies_performance f JOIN dim_movies m ON m.sk_movie_id = f.sk_movie_id
WHERE f.qtd_tmdb >= 100 AND f.popularidade IS NOT NULL
ORDER BY f.popularidade DESC LIMIT 5"""


@pytest.mark.parametrize(
    ("sql", "esperado"),
    [
        (SQL_Q04, {"qtd_tmdb"}),  # só no WHERE; popularidade também ordena
        ("SELECT titulo, receita_usd FROM t WHERE receita_usd IS NOT NULL ORDER BY receita_usd DESC", set()),
        (  # entra em coluna calculada
            (
                "SELECT titulo, receita_usd, (receita_usd - orcamento_usd) / receita_usd AS margem FROM t "
                "WHERE receita_usd >= 100000 ORDER BY margem DESC"
            ),
            set(),
        ),
        ("SELECT g, ROUND(AVG(x), 2) AS media FROM t WHERE x IS NOT NULL GROUP BY g", set()),
        ("", set()),
    ],
    ids=["q04", "ordena", "calculada", "agregada", "vazio"],
)
def test_colunas_so_filtro(sql: str, esperado: set[str]) -> None:
    assert run_eval.colunas_so_filtro(sql) == esperado


def test_coluna_so_de_filtro_ausente_nao_reprova() -> None:
    item = ITEM_EXATO | {"sql_esperado": SQL_Q04}
    esperado = {"colunas": ["titulo", "popularidade", "qtd_tmdb"], "linhas": [["A", 9.5, 1000], ["B", 8.0, 200]]}
    cols, linhas = resultado(["titulo_ano", "popularidade"], [["A (2023)", 9.5], ["B (2022)", 8.0]])
    acertou, detalhe = run_eval.comparar(item, esperado, cols, linhas, SQL)
    assert acertou and "opcionais ausentes: qtd_tmdb" in detalhe
    cols, linhas = resultado(["titulo_ano", "qtd"], [["A (2023)", 1000], ["B (2022)", 200]])
    assert not run_eval.comparar(item, esperado, cols, linhas, SQL)[0]  # a métrica (popularidade) segue obrigatória


# --- top_n_contem ---

def test_top_n_contem() -> None:
    assert comparar(ITEM_TOP, ["filme"], [["Filme B"], ["Filme X"], ["Filme A"]])[0]
    acertou, detalhe = comparar(ITEM_TOP, ["filme"], [["Filme A"], ["Filme C"]])
    assert not acertou and "Filme B" in detalhe


def test_top_n_com_chave_composta() -> None:
    item = {"id": "q09", "tipo_checagem": "top_n_contem", "chave": ["ator", "diretor"], "top_n": 1}
    esperado = {"colunas": ["ator", "diretor", "filmes"], "linhas": [["Joe", "Kevin", 37], ["Colby", "Kevin", 32]]}
    cols, linhas = resultado(["nome_diretor", "nome_ator", "qtd"], [["Kevin", "Joe", 37]])
    assert run_eval.comparar(item, esperado, cols, linhas, SQL)[0]
    cols, linhas = resultado(["nome_diretor", "nome_ator", "qtd"], [["Kevin", "Colby", 32]])
    assert not run_eval.comparar(item, esperado, cols, linhas, SQL)[0]


# --- recusa e casos sem SQL ---

def test_recusa() -> None:
    assert run_eval.comparar(ITEM_RECUSA, None, [], [], [], recusada=True)[0]
    assert run_eval.comparar(ITEM_RECUSA, None, [], [], [])[0]  # sem SQL também é recusa
    acertou, detalhe = run_eval.comparar(ITEM_RECUSA, None, ["x"], [{"x": 1}], SQL)
    assert not acertou and "em vez de recusar" in detalhe


def test_pergunta_normal_sem_sql_ou_sem_linhas_erra() -> None:
    assert not comparar(ITEM_EXATO, [], [], sqls=[])[0]
    assert not comparar(ITEM_EXATO, ["titulo"], [])[0]


# --- --ids ---

@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("q01,q02", ["q01", "q02"]),
        ("q01-q03", ["q01", "q02", "q03"]),
        ("q01-q02,q09, q01", ["q01", "q02", "q09"]),
        ("7", ["q07"]),
    ],
)
def test_parse_ids(texto: str, esperado: list[str]) -> None:
    assert run_eval.parse_ids(texto) == esperado


def test_parse_ids_invalido() -> None:
    with pytest.raises(ValueError, match="id inválido"):
        run_eval.parse_ids("qxx")


def test_selecionar_ids_fora_do_golden() -> None:
    with pytest.raises(ValueError, match="q99"):
        run_eval.selecionar(run_eval.carregar_golden(), ["q01", "q99"])


# --- Fluxo (ask falso, sem modelo nem banco) ---

GOLDEN_FALSO = [
    {"id": "q01", "categoria": "financas", "pergunta": "Top filmes?", "sql_esperado": "SELECT 1",
     "tipo_checagem": "top_n_contem", "chave": "titulo", "top_n": 1},
    {"id": "q02", "categoria": "financas", "pergunta": "Outra pergunta?", "sql_esperado": "SELECT 2",
     "tipo_checagem": "top_n_contem", "chave": "titulo", "top_n": 1},
    {"id": "q03", "categoria": "fora_escopo", "pergunta": "Previsão do tempo?", "sql_esperado": "",
     "tipo_checagem": "recusa"},
]
MODELO_FALSO = FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")]), model_name="modelo-teste")


def resposta_falsa(sql: str | None = "SELECT titulo FROM t", requisicoes: int = 2) -> RespostaAgente:
    """Resposta do agente com um SQL e o Filme A no resultado."""
    return RespostaAgente(
        resposta="ok", sql_executados=[sql] if sql else [], colunas=["titulo"] if sql else [],
        linhas=[{"titulo": "Filme A"}] if sql else [], modelo="modelo-teste",
        requisicoes=requisicoes, tempo_ms=1500, historico=[],
    )


@pytest.fixture
def ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Pastas temporárias, esperado falso, cota folgada e ask/run_query falsos."""
    estado: dict[str, Any] = {"chamadas": [], "cota": 40, "falhar_em": None}

    def ask_falso(pergunta: str, modelo: Any = None, usar_cache: bool = True) -> RespostaAgente:
        estado["chamadas"].append(pergunta)
        if pergunta == estado["falhar_em"]:
            raise QuotaExceededError()
        return resposta_falsa(None if "tempo" in pergunta else "SELECT titulo FROM t")

    def run_query_falso(sql: str, tempo_limite_s: float | None = None) -> Any:
        raise run_eval.ErroConsulta("sem banco no teste")  # cai no resultado guardado na resposta

    monkeypatch.setattr(run_eval, "ask", ask_falso)
    monkeypatch.setattr(run_eval, "run_query", run_query_falso)
    monkeypatch.setattr(run_eval, "cota_restante", lambda: estado["cota"])
    monkeypatch.setattr(run_eval, "PASTA_RESULTADOS", tmp_path / "resultados")
    monkeypatch.setattr(run_eval, "CAMINHO_RELATORIO", tmp_path / "report.md")
    monkeypatch.setattr(
        run_eval, "_carregar_esperado", lambda _id: {"colunas": ["titulo"], "linhas": [["Filme A"]]}
    )
    return estado


def test_rodar_salva_cada_pergunta_e_gera_relatorio(ambiente: dict[str, Any], tmp_path: Path) -> None:
    registros, parada = run_eval.rodar(GOLDEN_FALSO, MODELO_FALSO)
    assert parada is None
    assert [r["acertou"] for r in registros] == [True, True, True]
    assert sorted(p.name for p in (tmp_path / "resultados").iterdir()) == ["q01.json", "q02.json", "q03.json"]
    texto = run_eval.escrever_relatorio(GOLDEN_FALSO).read_text(encoding="utf-8")
    assert "**Acerto total:** 3/3 (100,0%)" in texto
    assert "Média de requisições por pergunta:** 2,0" in texto
    assert "`modelo-teste` (3)" in texto


def test_cota_esgotada_salva_o_que_rodou_e_retoma(
    ambiente: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ambiente["falhar_em"] = "Outra pergunta?"
    registros, parada = run_eval.rodar(GOLDEN_FALSO, MODELO_FALSO)
    assert [r["id"] for r in registros] == ["q01"] and "cota esgotada" in (parada or "")
    assert (tmp_path / "resultados" / "q01.json").exists()

    # Nova execução pelo main: pula a q01 já avaliada e continua.
    ambiente["falhar_em"] = None
    ambiente["chamadas"].clear()
    monkeypatch.setattr(run_eval, "carregar_golden", lambda: GOLDEN_FALSO)
    monkeypatch.setattr(run_eval, "_modelo_padrao", lambda: MODELO_FALSO)
    monkeypatch.setattr(run_eval, "inicializar_banco", lambda: None)
    assert run_eval.main([]) == 0
    assert ambiente["chamadas"] == ["Outra pergunta?", "Previsão do tempo?"]


def test_erro_http_do_modelo_para_sem_salvar(ambiente: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def ask_502(pergunta: str, modelo: Any = None, usar_cache: bool = True) -> RespostaAgente:
        if pergunta == "Outra pergunta?":
            raise ModelHTTPError(502, "modelo", {"message": "Provider returned error"})
        return resposta_falsa()

    monkeypatch.setattr(run_eval, "ask", ask_502)
    registros, parada = run_eval.rodar(GOLDEN_FALSO, MODELO_FALSO)
    assert [r["id"] for r in registros] == ["q01"]
    assert "erro do modelo em q02" in (parada or "")


def test_refazer_reavalia_as_ja_salvas(ambiente: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_eval, "carregar_golden", lambda: GOLDEN_FALSO)
    monkeypatch.setattr(run_eval, "_modelo_padrao", lambda: MODELO_FALSO)
    monkeypatch.setattr(run_eval, "inicializar_banco", lambda: None)
    assert run_eval.main(["--ids", "q01"]) == 0
    assert run_eval.main(["--ids", "q01"]) == 0
    assert ambiente["chamadas"] == ["Top filmes?"]  # a segunda execução pulou
    assert run_eval.main(["--ids", "q01", "--refazer"]) == 0
    assert ambiente["chamadas"] == ["Top filmes?", "Top filmes?"]


def test_para_quando_resta_pouca_cota_mas_nao_aborta_antes(
    ambiente: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_eval, "carregar_golden", lambda: GOLDEN_FALSO)
    monkeypatch.setattr(run_eval, "_modelo_padrao", lambda: MODELO_FALSO)
    monkeypatch.setattr(run_eval, "inicializar_banco", lambda: None)
    ambiente["cota"] = 3  # menor que a estimativa total (6), mas dá para começar
    original = run_eval.avaliar_pergunta

    def gasta_cota(*args: Any, **kwargs: Any) -> dict[str, Any]:
        ambiente["cota"] -= 2
        return original(*args, **kwargs)

    monkeypatch.setattr(run_eval, "avaliar_pergunta", gasta_cota)
    assert run_eval.main([]) == 2  # parou antes do fim
    assert ambiente["chamadas"] == ["Top filmes?"]


def test_dry_run_nao_chama_o_agente(ambiente: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(run_eval, "_modelo_padrao", lambda: MODELO_FALSO)
    assert run_eval.main(["--dry-run"]) == 0
    saida = capsys.readouterr().out
    assert ambiente["chamadas"] == []
    assert "guardrails" in saida  # q19 e q20 do golden set real custam 0
    assert "Estimativa: ~" in saida


def test_relatorio_mostra_sql_gerado_e_esperado_nas_erradas(ambiente: dict[str, Any]) -> None:
    registro = run_eval.avaliar_pergunta(GOLDEN_FALSO[0], MODELO_FALSO) | {
        "acertou": False, "detalhe": "faltam X", "sql_gerado": "SELECT a < b",
    }
    texto = run_eval.gerar_relatorio({"q01": registro}, GOLDEN_FALSO)
    assert "### q01" in texto and "SELECT a &lt; b" in texto and "SELECT 1" in texto
    assert "Ainda não avaliadas:** q02, q03" in texto


# --- Integração com o agente de verdade e modelo falso (precisa do banco) ---

@precisa_db
def test_avaliar_pergunta_com_function_model() -> None:
    respostas = iter([
        ModelResponse(parts=[ToolCallPart("run_sql", {"sql": "SELECT nome_genero FROM dim_genres"})]),
        ModelResponse(parts=[TextPart("São 19 gêneros.")]),
    ])
    modelo = FunctionModel(lambda m, i: next(respostas), model_name="modelo-teste")
    item = {"id": "q10", "categoria": "generos_produtoras", "pergunta": "Quais gêneros existem?",
            "sql_esperado": "SELECT 1", "tipo_checagem": "top_n_contem", "chave": "nome_genero", "top_n": 3}
    registro = run_eval.avaliar_pergunta(item, modelo)
    assert registro["acertou"], registro["detalhe"]  # os 3 gêneros mais frequentes estão entre os 19
    assert registro["requisicoes"] == 2 and registro["modelo"] == "modelo-teste"
    assert registro["sql_gerado"].startswith("SELECT nome_genero")
