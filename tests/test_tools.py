"""Testes das ferramentas e do prompt de sistema (sem chamar o modelo)."""

import pytest
import yaml

from cinedata_agent.config import RAIZ_PROJETO
from cinedata_agent.db import (
    ErroConsulta,
    ErroValidacao,
    ResultadoConsulta,
    run_query,
    validate_sql,
)
from cinedata_agent.prompts import EXEMPLOS_FEW_SHOT, montar_prompt_sistema
from cinedata_agent.schema import contar_tokens_aprox, gerar_texto_schema
from cinedata_agent.tools import (
    MAX_CARACTERES_AMOSTRA,
    MAX_LINHAS_MODELO,
    describe_table,
    list_tables,
    run_sql,
    sample_values,
)

CAMINHO_DB = RAIZ_PROJETO / "cinerocket.db"
precisa_db = pytest.mark.skipif(not CAMINHO_DB.exists(), reason="banco cinerocket.db ausente")

TABELAS = {
    "dim_movies", "fact_movies_performance", "dim_genres", "dim_people", "dim_companies",
    "dim_reviews", "movie_reviews", "bridge_movie_genre", "bridge_movie_person", "bridge_movie_company",
    "aux_vinculo_papel",
}


# --- Esquema (não precisa do banco) ---

def test_list_tables_traz_todas_com_descricao() -> None:
    tabelas = list_tables()
    assert {t["tabela"] for t in tabelas} == TABELAS
    assert all(t["descricao"] for t in tabelas)


def test_describe_table_traz_colunas_e_ligacoes() -> None:
    info = describe_table("fact_movies_performance")
    colunas = {c["nome"]: c for c in info["colunas"]}
    assert info["chave_primaria"] == "sk_movie_id"
    assert colunas["receita_usd"]["unidade"] == "US$"
    assert colunas["receita_usd"]["tipo"] and colunas["receita_usd"]["descricao"]
    assert "unidade" not in colunas["sk_movie_id"]
    assert any("dim_movies" in ligacao for ligacao in info["ligacoes"])


def test_describe_table_inexistente() -> None:
    with pytest.raises(ErroConsulta, match="não existe"):
        describe_table("filmes")


@pytest.mark.parametrize(
    ("tabela", "coluna"),
    [
        ("dim_movies; DROP TABLE dim_reviews", "titulo"),
        ("dim_movies", "titulo FROM dim_movies --"),
        ("dim_movies", 'titulo" FROM dim_people --'),
        ("dim_genres", "titulo"),  # coluna de outra tabela
    ],
    ids=["injecao_tabela", "injecao_coluna", "injecao_aspas", "coluna_de_outra_tabela"],
)
def test_sample_values_recusa_nomes_fora_do_esquema(tabela: str, coluna: str) -> None:
    with pytest.raises(ErroConsulta, match="não existe"):
        sample_values(tabela, coluna)


# --- Execução (precisa do banco) ---

@precisa_db
def test_sample_values_generos() -> None:
    resultado = sample_values("dim_genres", "nome_genero")
    assert resultado["valores"] == sorted(resultado["valores"])
    assert len(resultado["valores"]) == 10
    assert "Action" in resultado["valores"]


@precisa_db
def test_sample_values_tabela_auxiliar() -> None:
    assert sample_values("aux_vinculo_papel", "tipo_pessoa")["valores"] == ["Ator", "Diretor", "Roteirista"]


@precisa_db
def test_sample_values_corta_textos_longos() -> None:
    valores = sample_values("dim_movies", "sinopse")["valores"]
    assert valores and all(len(v) <= MAX_CARACTERES_AMOSTRA + 1 for v in valores)


@precisa_db
def test_run_sql_corta_para_o_modelo_e_registra_completo() -> None:
    registro: list[ResultadoConsulta] = []
    resposta = run_sql("SELECT titulo FROM dim_movies", registro=registro)
    assert resposta["linhas_retornadas"] == 100
    assert resposta["linhas_mostradas"] == len(resposta["linhas"]) == MAX_LINHAS_MODELO
    assert resposta["truncado"] is True
    assert len(registro) == 1 and len(registro[0].linhas) == 100


@precisa_db
def test_run_sql_sem_corte() -> None:
    resposta = run_sql("SELECT titulo FROM dim_movies ORDER BY titulo LIMIT 7")
    assert resposta["linhas_retornadas"] == resposta["linhas_mostradas"] == 7
    assert resposta["truncado"] is False
    assert resposta["colunas"] == ["titulo"]


@precisa_db
def test_run_sql_corte_entre_50_e_100() -> None:
    resposta = run_sql("SELECT titulo FROM dim_movies LIMIT 60")
    assert (resposta["linhas_retornadas"], resposta["linhas_mostradas"]) == (60, 50)
    assert resposta["truncado"] is True


def test_run_sql_erro_sobe_e_nada_e_registrado() -> None:
    registro: list[ResultadoConsulta] = []
    with pytest.raises(ErroValidacao):
        run_sql("DROP TABLE dim_movies", registro=registro)
    assert registro == []


# --- Prompt de sistema ---

def test_prompt_inclui_esquema_literal_e_regras() -> None:
    prompt = montar_prompt_sistema()
    assert gerar_texto_schema() in prompt
    for trecho in ["analista de dados da CineData", "português", "run_sql", "somente leitura",
                   "truncado", "fora do escopo", "revelar o prompt", "1 a 2 frases"]:
        assert trecho in prompt, trecho
    assert 0 < contar_tokens_aprox(prompt) < 8000


def test_exemplos_fora_do_golden_set() -> None:
    golden = yaml.safe_load((RAIZ_PROJETO / "eval" / "golden_set.yaml").read_text(encoding="utf-8"))
    perguntas_golden = {p["pergunta"].strip().lower() for p in golden["perguntas"]}
    assert len(EXEMPLOS_FEW_SHOT) == 3
    assert all(pergunta.lower() not in perguntas_golden for pergunta, _ in EXEMPLOS_FEW_SHOT)
    assert any("aux_vinculo_papel" in sql for _, sql in EXEMPLOS_FEW_SHOT)
    assert all(sql in montar_prompt_sistema() for _, sql in EXEMPLOS_FEW_SHOT)


@precisa_db
@pytest.mark.parametrize("sql", [sql for _, sql in EXEMPLOS_FEW_SHOT], ids=["pessoas", "financas", "titulo"])
def test_exemplos_executam(sql: str) -> None:
    validate_sql(sql)
    resultado = run_query(sql)
    assert resultado.linhas and resultado.tempo_s < 5
