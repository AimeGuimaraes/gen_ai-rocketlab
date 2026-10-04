"""Testes do dicionário de dados e do texto de esquema (sem chamar o modelo)."""

import sqlite3

import pytest

from cinedata_agent.config import RAIZ_PROJETO
from cinedata_agent.schema import (
    carregar_schema,
    contar_tokens_aprox,
    gerar_texto_schema,
)

TABELAS = {
    "dim_movies", "fact_movies_performance", "dim_genres", "dim_people", "dim_companies",
    "dim_reviews", "movie_reviews", "bridge_movie_genre", "bridge_movie_person", "bridge_movie_company",
}
CAMINHO_DB = RAIZ_PROJETO / "cinerocket.db"


def test_yaml_tem_as_10_tabelas_completas() -> None:
    tabelas = carregar_schema()["tabelas"]
    assert set(tabelas) == TABELAS
    for info in tabelas.values():
        assert {"descricao", "chave_primaria", "ligacoes", "colunas"} <= set(info)
        assert all(col.get("descricao") for col in info["colunas"].values())


def test_regras_cobrem_as_14_perguntas() -> None:
    ids = [regra["id"] for regra in carregar_schema()["regras_de_negocio"]]
    assert [f"P{n}" for n in range(1, 15)] == [i for i in ids if i.startswith("P")]
    assert len(ids) == len(set(ids))


@pytest.mark.skipif(not CAMINHO_DB.exists(), reason="banco cinerocket.db ausente")
def test_colunas_do_yaml_batem_com_o_banco() -> None:
    tabelas = carregar_schema()["tabelas"]
    conexao = sqlite3.connect(f"file:{CAMINHO_DB.as_posix()}?mode=ro", uri=True)
    try:
        for tabela, info in tabelas.items():
            colunas_db = {linha[1] for linha in conexao.execute(f"PRAGMA table_info({tabela})")}
            assert set(info["colunas"]) == colunas_db, tabela
    finally:
        conexao.close()


def test_texto_gerado_tem_tabelas_e_regras() -> None:
    texto = gerar_texto_schema()
    assert all(f"### {tabela} " in texto for tabela in TABELAS)
    assert "P14 " in texto and "G1 " in texto
    assert "limitacoes_conhecidas" not in texto and "notas_desempenho" not in texto
    assert 0 < contar_tokens_aprox(texto) < 8000
