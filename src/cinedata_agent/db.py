"""Camada de banco: cópia em memória somente leitura, validação de SQL e execução com limite de tempo."""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import sqlglot
from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import ParseError

from cinedata_agent.config import carregar_config

logger = logging.getLogger(__name__)

LIMITE_PADRAO = 100  # LIMIT inserido quando a consulta não tem LIMIT
MAX_LINHAS = 1000  # teto de linhas devolvidas, mesmo com LIMIT do usuário
TEMPO_LIMITE_S = 5.0  # tempo máximo por consulta
INSTRUCOES_POR_CHECAGEM = 10_000  # frequência do progress handler (instruções da VM do SQLite)

# Comandos que nunca podem aparecer na árvore do SQL (REPLACE e VACUUM viram exp.Command).
NOS_PROIBIDOS: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create,
    exp.Attach, exp.Detach, exp.Pragma, exp.Command,
)

SQL_CRIAR_AUX = """
CREATE TABLE aux_vinculo_papel (
    id_mov INTEGER NOT NULL,
    id_pes INTEGER NOT NULL,
    tipo_pessoa TEXT NOT NULL
)
"""
SQL_PREENCHER_AUX = """
INSERT INTO aux_vinculo_papel (id_mov, id_pes, tipo_pessoa)
SELECT m.rowid, p.rowid, p.tipo_pessoa
FROM bridge_movie_person bp
JOIN dim_movies m ON m.sk_movie_id = bp.sk_movie_id
JOIN dim_people p ON p.sk_person_id = bp.sk_person_id
ORDER BY p.tipo_pessoa, m.rowid, p.rowid
"""
SQL_INDICE_AUX = (
    "CREATE INDEX ix_aux_vinculo_papel ON aux_vinculo_papel (tipo_pessoa, id_mov, id_pes)"
)


class ResultadoConsulta(BaseModel):
    """Resultado de uma consulta executada com sucesso."""

    sql_executado: str
    colunas: list[str]
    linhas: list[dict[str, Any]]
    total_linhas: int
    truncado: bool
    tempo_s: float


class EstatisticasBanco(BaseModel):
    """Custo da inicialização do banco em memória."""

    tempo_inicializacao_s: float
    memoria_mb: float
    linhas_aux: int


class ErroConsulta(Exception):
    """Erro de consulta com mensagem clara em português (o agente usa para se corrigir)."""


class ErroValidacao(ErroConsulta):
    """SQL recusado pela validação (não é um SELECT único)."""


class ErroTempoEsgotado(ErroConsulta):
    """Consulta cancelada por passar do tempo máximo."""


_conexao: sqlite3.Connection | None = None
_estatisticas: EstatisticasBanco | None = None
_trava_init = threading.Lock()
_trava_consulta = threading.Lock()


def _memoria_mb() -> float:
    """Memória residente do processo, em MB."""
    return psutil.Process().memory_info().rss / 1024**2


def _copiar_para_memoria(db_path: Path) -> sqlite3.Connection:
    """Abre o arquivo somente leitura e copia o banco inteiro para :memory:."""
    if not db_path.exists():
        raise FileNotFoundError(f"Banco não encontrado em {db_path}. Confira DB_PATH no .env.")
    origem = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        destino = sqlite3.connect(":memory:", check_same_thread=False)
        origem.backup(destino)
    finally:
        origem.close()
    return destino


def _preparar_conexao(conexao: sqlite3.Connection) -> int:
    """Aplica os PRAGMAs, cria aux_vinculo_papel e trava a escrita; devolve as linhas da auxiliar."""
    conexao.execute("PRAGMA temp_store = MEMORY")
    conexao.execute(SQL_CRIAR_AUX)
    conexao.execute(SQL_PREENCHER_AUX)
    conexao.execute(SQL_INDICE_AUX)
    conexao.commit()
    linhas_aux = conexao.execute("SELECT COUNT(*) FROM aux_vinculo_papel").fetchone()[0]
    conexao.execute("PRAGMA query_only = ON")  # só depois de criar a auxiliar
    return linhas_aux


def inicializar_banco(db_path: Path | None = None) -> EstatisticasBanco:
    """Inicializa uma única vez por processo o banco em memória e devolve o custo da carga."""
    global _conexao, _estatisticas
    if _estatisticas is not None:
        return _estatisticas
    with _trava_init:
        if _estatisticas is not None:  # outra thread terminou enquanto esperávamos
            return _estatisticas
        caminho = db_path or carregar_config().db_path
        inicio, memoria_inicio = time.perf_counter(), _memoria_mb()
        conexao = _copiar_para_memoria(caminho)
        linhas_aux = _preparar_conexao(conexao)
        _estatisticas = EstatisticasBanco(
            tempo_inicializacao_s=round(time.perf_counter() - inicio, 2),
            memoria_mb=round(_memoria_mb() - memoria_inicio, 1),
            linhas_aux=linhas_aux,
        )
        _conexao = conexao
        logger.info(
            "Banco em memória pronto em %.2f s (+%.0f MB, aux_vinculo_papel com %d linhas).",
            _estatisticas.tempo_inicializacao_s, _estatisticas.memoria_mb, linhas_aux,
        )
        return _estatisticas


def obter_conexao() -> sqlite3.Connection:
    """Devolve a conexão em memória, inicializando o banco se preciso."""
    inicializar_banco()
    assert _conexao is not None
    return _conexao


def estatisticas() -> EstatisticasBanco | None:
    """Custo da inicialização, ou None se o banco ainda não foi carregado."""
    return _estatisticas


def validate_sql(sql: str) -> exp.Expression:
    """Valida que o SQL é um único SELECT (ou WITH ... SELECT) e devolve a árvore do sqlglot."""
    if not sql or not sql.strip():
        raise ErroValidacao("A consulta está vazia. Envie um comando SELECT.")
    try:
        comandos = [c for c in sqlglot.parse(sql, read="sqlite") if c is not None]
    except ParseError as erro:
        raise ErroValidacao(f"Erro de sintaxe no SQL: {erro}") from erro
    if not comandos:
        raise ErroValidacao("A consulta está vazia. Envie um comando SELECT.")
    if len(comandos) > 1:
        raise ErroValidacao(
            f"Envie apenas um comando SQL por vez (recebi {len(comandos)} separados por ponto e vírgula)."
        )
    arvore = comandos[0]
    proibido = next((no for no in arvore.walk() if isinstance(no, NOS_PROIBIDOS)), None)
    if proibido is not None or not isinstance(arvore, (exp.Select, exp.SetOperation)):
        raise ErroValidacao(
            f"Comando não permitido: {_nome_comando(proibido or arvore)}. O acesso é somente "
            "leitura; use apenas SELECT (ou WITH ... SELECT)."
        )
    return arvore


def _nome_comando(no: exp.Expression) -> str:
    """Nome legível do comando SQL de um nó (ex.: DROP, VACUUM)."""
    if isinstance(no, exp.Command):
        return str(no.this).upper()
    return no.key.upper()


def aplicar_limite(sql: str, arvore: exp.Expression) -> tuple[str, bool]:
    """Acrescenta LIMIT 100 se a consulta não tiver LIMIT; devolve o SQL e se o limite foi inserido."""
    if arvore.args.get("limit") is not None:
        return sql, False
    return _com_limite(sql, LIMITE_PADRAO), True


def _com_limite(sql: str, limite: int) -> str:
    """Acrescenta LIMIT ao fim do SQL, sem o ponto e vírgula final."""
    # A quebra de linha evita que o LIMIT caia dentro de um comentário "--" no fim da consulta.
    return f"{sql.strip().rstrip(';').rstrip()}\nLIMIT {limite}"


def _traduzir_erro(erro: sqlite3.Error, tempo_limite_s: float) -> ErroConsulta:
    """Converte um erro do SQLite em mensagem clara, com dica de correção."""
    mensagem = str(erro)
    if "interrupted" in mensagem:
        return ErroTempoEsgotado(
            f"A consulta passou de {tempo_limite_s:g} s e foi cancelada. Simplifique: filtre antes "
            "de juntar tabelas e, em perguntas sobre pessoas, use aux_vinculo_papel."
        )
    dicas = {
        "no such table": "Tabela inexistente. Use só as tabelas do esquema",
        "no such column": "Coluna inexistente. Confira o nome e a tabela no esquema",
        "ambiguous column name": "Coluna ambígua. Prefixe a coluna com o apelido da tabela",
        "syntax error": "Erro de sintaxe no SQL",
        "readonly": "O banco é somente leitura",
        "misuse of aggregate": "Uso indevido de função de agregação. Confira GROUP BY e HAVING",
    }
    for trecho, dica in dicas.items():
        if trecho in mensagem:
            return ErroConsulta(f"{dica} (SQLite: {mensagem}).")
    return ErroConsulta(f"Erro do SQLite: {mensagem}.")


def _nomes_unicos(colunas: list[str]) -> list[str]:
    """Renomeia colunas repetidas (titulo, titulo_2...) para não sumirem nos dicionários."""
    vistos: dict[str, int] = {}
    unicos = []
    for nome in colunas:
        vistos[nome] = vistos.get(nome, 0) + 1
        unicos.append(nome if vistos[nome] == 1 else f"{nome}_{vistos[nome]}")
    return unicos


def run_query(sql: str, tempo_limite_s: float = TEMPO_LIMITE_S) -> ResultadoConsulta:
    """Valida e executa um SELECT no banco em memória, com LIMIT automático e tempo máximo."""
    arvore = validate_sql(sql)
    sql_executado, limite_inserido = aplicar_limite(sql, arvore)
    # Com LIMIT inserido, busca uma linha a mais só para saber se o resultado foi cortado.
    sql_real = _com_limite(sql, LIMITE_PADRAO + 1) if limite_inserido else sql
    maximo = LIMITE_PADRAO if limite_inserido else MAX_LINHAS

    conexao = obter_conexao()
    with _trava_consulta:
        prazo = time.perf_counter() + tempo_limite_s
        conexao.set_progress_handler(lambda: int(time.perf_counter() > prazo), INSTRUCOES_POR_CHECAGEM)
        inicio = time.perf_counter()
        try:
            cursor = conexao.execute(sql_real)
            linhas = cursor.fetchmany(maximo + 1)
            colunas = _nomes_unicos([d[0] for d in cursor.description or []])
        except sqlite3.Error as erro:
            raise _traduzir_erro(erro, tempo_limite_s) from erro
        finally:
            conexao.set_progress_handler(None, 0)
        tempo = time.perf_counter() - inicio

    truncado = len(linhas) > maximo
    linhas = linhas[:maximo]
    logger.info("Consulta executada em %.3f s: %d linhas%s.", tempo, len(linhas), " (truncado)" if truncado else "")
    return ResultadoConsulta(
        sql_executado=sql_executado,
        colunas=colunas,
        linhas=[dict(zip(colunas, linha, strict=True)) for linha in linhas],
        total_linhas=len(linhas),
        truncado=truncado,
        tempo_s=round(tempo, 3),
    )
