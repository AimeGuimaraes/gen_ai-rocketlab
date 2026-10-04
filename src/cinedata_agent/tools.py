"""Ferramentas do agente: funções puras (sem PydanticAI) para explorar o esquema e consultar o banco."""

import logging
from functools import lru_cache
from typing import Any

from cinedata_agent.db import ErroConsulta, ResultadoConsulta, run_query
from cinedata_agent.schema import carregar_schema

logger = logging.getLogger(__name__)

MAX_LINHAS_MODELO = 50  # linhas enviadas ao modelo (o resultado completo fica no registro)
MAX_VALORES_AMOSTRA = 10
MAX_CARACTERES_AMOSTRA = 100  # textos longos (ex.: sinopse) são cortados na amostra


@lru_cache(maxsize=1)
def _tabelas() -> dict[str, dict[str, Any]]:
    """Tabelas do dicionário de dados, incluindo as auxiliares criadas em memória."""
    schema = carregar_schema()
    return {**schema["tabelas"], **schema.get("tabelas_auxiliares", {})}


def _info_tabela(table_name: str) -> dict[str, Any]:
    """Devolve a entrada da tabela no esquema ou levanta ErroConsulta com as opções válidas."""
    tabelas = _tabelas()
    if table_name not in tabelas:
        raise ErroConsulta(
            f"Tabela '{table_name}' não existe. Tabelas disponíveis: {', '.join(tabelas)}."
        )
    return tabelas[table_name]


def list_tables() -> list[dict[str, str]]:
    """Lista as tabelas do banco da CineData com uma descrição curta de cada uma.

    Use só se o esquema do prompt não bastar. Devolve [{"tabela", "descricao"}].
    """
    return [{"tabela": nome, "descricao": info["descricao"]} for nome, info in _tabelas().items()]


def describe_table(table_name: str) -> dict[str, Any]:
    """Descreve uma tabela: chave primária, colunas (tipo, descrição, unidade) e ligações com outras tabelas.

    Use quando precisar confirmar o nome ou o significado de uma coluna antes de escrever o SQL.
    """
    info = _info_tabela(table_name)
    colunas = []
    for nome, coluna in info["colunas"].items():
        item = {"nome": nome, "tipo": coluna.get("tipo", ""), "descricao": coluna["descricao"]}
        unidade = coluna.get("moeda") or coluna.get("unidade")
        if unidade:
            item["unidade"] = unidade
        colunas.append(item)
    return {
        "tabela": table_name,
        "descricao": info["descricao"],
        "chave_primaria": info["chave_primaria"],
        "colunas": colunas,
        "ligacoes": list(info["ligacoes"]),
    }


def _cortar(valor: Any) -> Any:
    """Corta textos longos para a amostra não gastar contexto."""
    if isinstance(valor, str) and len(valor) > MAX_CARACTERES_AMOSTRA:
        return valor[:MAX_CARACTERES_AMOSTRA] + "…"
    return valor


def sample_values(table_name: str, column_name: str) -> dict[str, Any]:
    """Mostra até 10 valores distintos (não nulos) de uma coluna, em ordem.

    Use para descobrir a grafia exata de valores (ex.: status_filme, tipo_pessoa) antes de filtrar.
    """
    info = _info_tabela(table_name)
    if column_name not in info["colunas"]:
        raise ErroConsulta(
            f"Coluna '{column_name}' não existe em {table_name}. "
            f"Colunas disponíveis: {', '.join(info['colunas'])}."
        )
    # Seguro: tabela e coluna foram conferidas contra o esquema acima (lista branca).
    sql = (
        f'SELECT DISTINCT "{column_name}" AS valor FROM "{table_name}" '
        f'WHERE "{column_name}" IS NOT NULL ORDER BY 1 LIMIT {MAX_VALORES_AMOSTRA}'
    )
    resultado = run_query(sql)
    return {
        "tabela": table_name,
        "coluna": column_name,
        "valores": [_cortar(linha["valor"]) for linha in resultado.linhas],
    }


def resumir_para_modelo(resultado: ResultadoConsulta) -> dict[str, Any]:
    """Monta o que vai ao modelo: no máximo 50 linhas, com as contagens e o aviso de corte."""
    linhas = resultado.linhas[:MAX_LINHAS_MODELO]
    return {
        "sql_executado": resultado.sql_executado,
        "colunas": resultado.colunas,
        "linhas": linhas,
        "linhas_retornadas": resultado.total_linhas,
        "linhas_mostradas": len(linhas),
        "truncado": resultado.truncado or resultado.total_linhas > len(linhas),
    }


def run_sql(sql: str, registro: list[ResultadoConsulta] | None = None) -> dict[str, Any]:
    """Executa uma consulta SQL (SQLite) somente leitura no banco da CineData e devolve o resultado.

    Aceita só um SELECT (ou WITH ... SELECT). Sem LIMIT, é aplicado LIMIT 100. Devolve no máximo
    50 linhas; se "truncado" for true, há mais linhas do que as mostradas. Em perguntas sobre
    pessoas (atores, diretores, roteiristas), use aux_vinculo_papel, que é muito mais rápida.
    """
    resultado = run_query(sql)
    if registro is not None:
        registro.append(resultado)  # resultado completo (até 100 linhas) para mostrar ao usuário
    return resumir_para_modelo(resultado)
