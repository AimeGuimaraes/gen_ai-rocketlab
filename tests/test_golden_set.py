"""Testes de estrutura do golden set (sem chamar o modelo e sem rodar as consultas)."""

import json

import pytest
import sqlglot
import yaml
from sqlglot import exp

from cinedata_agent.config import RAIZ_PROJETO

CAMINHO_GOLDEN = RAIZ_PROJETO / "eval" / "golden_set.yaml"
PASTA_EXPECTED = RAIZ_PROJETO / "eval" / "expected"
TIPOS = {"resultado_exato", "top_n_contem", "recusa"}


def _perguntas() -> list[dict]:
    """Lê as perguntas do golden set."""
    with open(CAMINHO_GOLDEN, encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)["perguntas"]


def _chaves(item: dict) -> list[str]:
    """Devolve a chave do item sempre como lista."""
    chave = item["chave"]
    return chave if isinstance(chave, list) else [chave]


def test_ids_e_campos() -> None:
    perguntas = _perguntas()
    assert [p["id"] for p in perguntas] == [f"q{n:02d}" for n in range(1, 21)]
    for p in perguntas:
        assert {"id", "categoria", "pergunta", "sql_esperado", "tipo_checagem", "observacao"} <= set(p)
        assert p["tipo_checagem"] in TIPOS, p["id"]


def test_coerencia_entre_tipo_sql_chave_e_top_n() -> None:
    for p in _perguntas():
        tem_sql = bool(p["sql_esperado"].strip())
        assert tem_sql == (p["tipo_checagem"] != "recusa"), p["id"]
        assert tem_sql == ("chave" in p), p["id"]
        assert ("top_n" in p) == (p["tipo_checagem"] == "top_n_contem"), p["id"]


def test_campo_informativa() -> None:
    perguntas = _perguntas()
    for p in perguntas:
        assert isinstance(p.get("informativa", False), bool), p["id"]
    informativas = [p["id"] for p in perguntas if p.get("informativa")]
    assert informativas == ["q17"]


@pytest.mark.parametrize("item", [p for p in _perguntas() if p["sql_esperado"].strip()], ids=lambda p: p["id"])
def test_sql_e_um_unico_select(item: dict) -> None:
    comandos = sqlglot.parse(item["sql_esperado"], read="sqlite")
    assert len(comandos) == 1
    assert isinstance(comandos[0], exp.Select)


@pytest.mark.parametrize("item", [p for p in _perguntas() if p["sql_esperado"].strip()], ids=lambda p: p["id"])
def test_chave_existe_no_resultado_esperado(item: dict) -> None:
    caminho = PASTA_EXPECTED / f"{item['id']}.json"
    if not caminho.exists():
        pytest.skip("resultado esperado ainda não gerado (rode eval/build_expected.py)")
    esperado = json.loads(caminho.read_text(encoding="utf-8"))
    assert set(_chaves(item)) <= set(esperado["colunas"])
    assert esperado["qtd_linhas"] >= item.get("top_n", 1)
