"""Testes da camada de banco: validação, LIMIT, tempo máximo, somente leitura e desempenho."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from cinedata_agent.config import RAIZ_PROJETO
from cinedata_agent.db import (
    LIMITE_PADRAO,
    ErroConsulta,
    ErroTempoEsgotado,
    ErroValidacao,
    inicializar_banco,
    obter_conexao,
    run_query,
    validate_sql,
)
from cinedata_agent.schema import carregar_schema

CAMINHO_DB = RAIZ_PROJETO / "cinerocket.db"
PASTA_EXPECTED = RAIZ_PROJETO / "eval" / "expected"
precisa_db = pytest.mark.skipif(not CAMINHO_DB.exists(), reason="banco cinerocket.db ausente")

# Versões de P7, P8 e P9 com aux_vinculo_papel (regras em docs/schema_docs.yaml).
SQL_P7 = """
SELECT p.nome_pessoa AS nome, COUNT(DISTINCT a.id_mov) AS qtd_filmes
FROM aux_vinculo_papel a
JOIN dim_movies m ON m.rowid = a.id_mov
JOIN dim_people p ON p.rowid = a.id_pes
WHERE a.tipo_pessoa = 'Ator'
  AND m.data_lancamento BETWEEN date('now', '-5 years') AND date('now')
GROUP BY a.id_pes
ORDER BY qtd_filmes DESC, nome
LIMIT 10
"""
SQL_P8 = """
SELECT p.nome_pessoa AS nome,
       ROUND(AVG(f.nota_tmdb), 2) AS nota_media_tmdb,
       COUNT(*) AS qtd_filmes
FROM aux_vinculo_papel a
JOIN dim_movies m ON m.rowid = a.id_mov
JOIN fact_movies_performance f ON f.sk_movie_id = m.sk_movie_id
JOIN dim_people p ON p.rowid = a.id_pes
WHERE a.tipo_pessoa = 'Diretor'
  AND f.qtd_tmdb >= 100
  AND m.data_lancamento <= date('now')
GROUP BY a.id_pes
HAVING COUNT(*) >= 5
ORDER BY nota_media_tmdb DESC, qtd_filmes DESC, nome
LIMIT 10
"""
SQL_P9 = """
SELECT pa.nome_pessoa AS ator, pd.nome_pessoa AS diretor, COUNT(*) AS filmes_juntos
FROM aux_vinculo_papel d
JOIN aux_vinculo_papel a ON a.tipo_pessoa = 'Ator' AND a.id_mov = d.id_mov
JOIN dim_people pa ON pa.rowid = a.id_pes
JOIN dim_people pd ON pd.rowid = d.id_pes
WHERE d.tipo_pessoa = 'Diretor'
  AND pa.nome_pessoa <> pd.nome_pessoa
GROUP BY a.id_pes, d.id_pes
ORDER BY filmes_juntos DESC, ator, diretor
LIMIT 10
"""


def _assinatura(caminho: Path) -> tuple[str, int, int]:
    """SHA-256, tamanho e data de modificação do arquivo."""
    sha = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            sha.update(bloco)
    info = caminho.stat()
    return sha.hexdigest(), info.st_size, info.st_mtime_ns


@pytest.fixture(scope="module")
def assinatura_inicial() -> tuple[str, int, int]:
    """Guarda a assinatura do cinerocket.db antes de carregar o banco em memória."""
    assinatura = _assinatura(CAMINHO_DB)
    inicializar_banco(CAMINHO_DB)
    return assinatura


@pytest.fixture
def banco(assinatura_inicial: tuple[str, int, int]) -> sqlite3.Connection:
    """Conexão em memória (inicializada uma única vez)."""
    return obter_conexao()


def _esperado(id_pergunta: str) -> list[list]:
    """Linhas esperadas do golden set (eval/expected)."""
    return json.loads((PASTA_EXPECTED / f"{id_pergunta}.json").read_text(encoding="utf-8"))["linhas"]


# --- Validação (não precisa do banco) ---

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT titulo FROM dim_movies",
        "WITH recentes AS (SELECT titulo FROM dim_movies WHERE ano_lancamento >= 2023) SELECT * FROM recentes",
        "SELECT 1 UNION SELECT 2",
        "SELECT titulo FROM dim_movies;",
    ],
    ids=["select", "with", "union", "ponto_e_virgula_final"],
)
def test_select_aceito(sql: str) -> None:
    validate_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE dim_movies",
        "DELETE FROM dim_movies",
        "UPDATE dim_movies SET titulo = 'x'",
        "INSERT INTO dim_genres VALUES ('x', 'y')",
        "REPLACE INTO dim_genres VALUES ('x', 'y')",
        "ATTACH DATABASE 'outro.db' AS outro",
        "DETACH DATABASE outro",
        "PRAGMA query_only = OFF",
        "VACUUM",
        "ALTER TABLE dim_movies ADD COLUMN x INT",
        "CREATE TABLE x (a INT)",
    ],
    ids=["drop", "delete", "update", "insert", "replace", "attach", "detach", "pragma", "vacuum", "alter", "create"],
)
def test_comando_de_escrita_bloqueado(sql: str) -> None:
    with pytest.raises(ErroValidacao, match="somente leitura"):
        validate_sql(sql)


def test_mensagem_cita_o_comando() -> None:
    with pytest.raises(ErroValidacao, match="DROP"):
        validate_sql("DROP TABLE dim_movies")


def test_dois_comandos_bloqueados() -> None:
    with pytest.raises(ErroValidacao, match="um comando"):
        validate_sql("SELECT 1; DROP TABLE dim_movies")


@pytest.mark.parametrize("sql", ["", "   ", ";"], ids=["vazio", "espacos", "so_ponto_e_virgula"])
def test_sql_vazio_recusado(sql: str) -> None:
    with pytest.raises(ErroValidacao, match="vazia"):
        validate_sql(sql)


def test_sintaxe_invalida_recusada() -> None:
    with pytest.raises(ErroValidacao, match="sintaxe"):
        validate_sql("SELECT FROM WHERE")


# Falsos positivos: palavras perigosas fora de comandos não podem ser bloqueadas.
FALSOS_POSITIVOS = [
    "SELECT REPLACE(titulo, 'a', 'b') FROM dim_movies",
    "SELECT titulo FROM dim_movies WHERE LOWER(titulo) LIKE '%drop table%'",
    'SELECT titulo AS delete_flag, ano_lancamento AS "update" FROM dim_movies',
    "SELECT titulo FROM dim_movies -- comentário",
]
IDS_FALSOS_POSITIVOS = ["replace_funcao", "drop_em_texto", "apelidos_reservados", "comentario_final"]


@pytest.mark.parametrize("sql", FALSOS_POSITIVOS, ids=IDS_FALSOS_POSITIVOS)
def test_falso_positivo_passa_na_validacao(sql: str) -> None:
    validate_sql(sql)


# --- Execução (precisa do banco) ---

@precisa_db
@pytest.mark.parametrize("sql", FALSOS_POSITIVOS, ids=IDS_FALSOS_POSITIVOS)
def test_falso_positivo_executa(banco: sqlite3.Connection, sql: str) -> None:
    resultado = run_query(sql)  # não levanta erro
    assert resultado.colunas and resultado.sql_executado.endswith(f"LIMIT {LIMITE_PADRAO}")


@precisa_db
def test_limit_inserido(banco: sqlite3.Connection) -> None:
    resultado = run_query("SELECT titulo, ano_lancamento FROM dim_movies")
    assert resultado.sql_executado.endswith(f"LIMIT {LIMITE_PADRAO}")
    assert resultado.total_linhas == len(resultado.linhas) == LIMITE_PADRAO
    assert resultado.truncado is True
    assert resultado.colunas == ["titulo", "ano_lancamento"]
    assert set(resultado.linhas[0]) == {"titulo", "ano_lancamento"}


@precisa_db
def test_limit_inserido_sem_truncar(banco: sqlite3.Connection) -> None:
    resultado = run_query("SELECT nome_genero FROM dim_genres")
    assert resultado.total_linhas == 19
    assert resultado.truncado is False


@precisa_db
def test_limit_existente_mantido(banco: sqlite3.Connection) -> None:
    sql = "SELECT titulo FROM dim_movies ORDER BY titulo LIMIT 7"
    resultado = run_query(sql)
    assert resultado.sql_executado == sql
    assert resultado.total_linhas == 7
    assert resultado.truncado is False


@precisa_db
def test_colunas_repetidas_nao_somem(banco: sqlite3.Connection) -> None:
    resultado = run_query("SELECT titulo, titulo FROM dim_movies LIMIT 1")
    assert resultado.colunas == ["titulo", "titulo_2"]


@precisa_db
@pytest.mark.parametrize(
    ("sql", "trecho"),
    [
        ("SELECT * FROM filmes", "Tabela inexistente"),
        ("SELECT nota FROM fact_movies_performance", "Coluna inexistente"),
        (
            (
                "SELECT sk_movie_id FROM dim_movies JOIN fact_movies_performance USING (sk_movie_id) "
                "JOIN dim_reviews r ON r.sk_movie_id = dim_movies.sk_movie_id"
            ),
            "Coluna ambígua",
        ),
    ],
    ids=["tabela", "coluna", "ambigua"],
)
def test_erro_do_sqlite_vira_mensagem_clara(banco: sqlite3.Connection, sql: str, trecho: str) -> None:
    with pytest.raises(ErroConsulta, match=trecho):
        run_query(sql)


@precisa_db
def test_tempo_esgotado(banco: sqlite3.Connection) -> None:
    infinita = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(ErroTempoEsgotado, match=r"limite de 0\.5 s \(DB_TIMEOUT_S\)"):
        run_query(infinita, tempo_limite_s=0.5)
    assert run_query("SELECT 1 AS um").linhas == [{"um": 1}]  # a conexão continua usável


@precisa_db
@pytest.mark.parametrize(
    "sql",
    ["CREATE TABLE invasora (a INT)", "INSERT INTO dim_genres VALUES ('x', 'y')", "DELETE FROM dim_reviews"],
    ids=["create", "insert", "delete"],
)
def test_escrita_recusada_sem_validacao(banco: sqlite3.Connection, sql: str) -> None:
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        banco.execute(sql)


@precisa_db
def test_query_only_ativo(banco: sqlite3.Connection) -> None:
    assert banco.execute("PRAGMA query_only").fetchone()[0] == 1
    assert banco.execute("PRAGMA temp_store").fetchone()[0] == 2  # 2 = MEMORY


@precisa_db
def test_inicializacao_unica(banco: sqlite3.Connection) -> None:
    assert inicializar_banco() is inicializar_banco()
    assert obter_conexao() is banco


@precisa_db
def test_aux_vinculo_papel_bate_com_yaml_e_bridge(banco: sqlite3.Connection) -> None:
    colunas_yaml = set(carregar_schema()["tabelas_auxiliares"]["aux_vinculo_papel"]["colunas"])
    colunas_db = {linha[1] for linha in banco.execute("PRAGMA table_info(aux_vinculo_papel)")}
    assert colunas_yaml == colunas_db
    total_aux = banco.execute("SELECT COUNT(*) FROM aux_vinculo_papel").fetchone()[0]
    total_bridge = banco.execute("SELECT COUNT(*) FROM bridge_movie_person").fetchone()[0]
    assert total_aux == total_bridge == inicializar_banco().linhas_aux


@precisa_db
@pytest.mark.parametrize(
    ("id_pergunta", "sql", "comparar_tudo"),
    [("q07", SQL_P7, False), ("q08", SQL_P8, True), ("q09", SQL_P9, True)],
    ids=["P7", "P8", "P9"],
)
def test_p7_p8_p9_rapidas_e_corretas(
    banco: sqlite3.Connection, id_pergunta: str, sql: str, comparar_tudo: bool
) -> None:
    # 10 s: folga para máquinas mais lentas (no mesmo processo, medem de 0,1 s a ~5 s).
    resultado = run_query(sql, tempo_limite_s=10)
    assert resultado.tempo_s < 10
    obtido = [list(linha.values()) for linha in resultado.linhas]
    esperado = _esperado(id_pergunta)
    # P7 depende de date('now'): compara só o 1º colocado, que é estável entre dias próximos.
    assert obtido == esperado if comparar_tudo else obtido[0] == esperado[0]


@precisa_db
def test_arquivo_do_banco_inalterado(assinatura_inicial: tuple[str, int, int]) -> None:
    """Deve ficar por último no arquivo: compara o cinerocket.db com a assinatura inicial."""
    assert _assinatura(CAMINHO_DB) == assinatura_inicial
