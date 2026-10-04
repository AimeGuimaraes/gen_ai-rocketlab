"""Gera os resultados esperados do golden set rodando cada sql_esperado no banco (somente leitura).

Uso:
    python eval/build_expected.py            # todas as perguntas com SQL
    python eval/build_expected.py --ids q09  # só algumas

Não chama o modelo de linguagem. Não há limite de tempo por consulta (a q09 é a mais lenta).
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from cinedata_agent.config import carregar_config

CAMINHO_GOLDEN = RAIZ / "eval" / "golden_set.yaml"
PASTA_EXPECTED = RAIZ / "eval" / "expected"
CACHE_KIB = 1_000_000  # ~1 GB de cache só em memória: acelera os joins com pessoas

logger = logging.getLogger("build_expected")


def carregar_golden(caminho: Path = CAMINHO_GOLDEN) -> list[dict[str, Any]]:
    """Lê o golden set e devolve a lista de perguntas."""
    with open(caminho, encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)["perguntas"]


def abrir_conexao(db_path: Path) -> sqlite3.Connection:
    """Abre o banco somente leitura, com cache grande e escrita recusada."""
    conexao = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conexao.execute("PRAGMA query_only = ON")
    conexao.execute(f"PRAGMA cache_size = -{CACHE_KIB}")
    return conexao


def executar(conexao: sqlite3.Connection, sql: str) -> tuple[list[str], list[list[Any]], float]:
    """Executa o SQL e devolve colunas, linhas e tempo em segundos."""
    inicio = time.perf_counter()
    cursor = conexao.execute(sql)
    linhas = [list(linha) for linha in cursor.fetchall()]
    tempo = time.perf_counter() - inicio
    colunas = [descricao[0] for descricao in cursor.description]
    return colunas, linhas, tempo


def salvar(item: dict[str, Any], colunas: list[str], linhas: list[list[Any]], tempo: float) -> Path:
    """Salva o resultado esperado em eval/expected/<id>.json."""
    destino = PASTA_EXPECTED / f"{item['id']}.json"
    dados = {
        "id": item["id"],
        "pergunta": item["pergunta"],
        "sql": item["sql_esperado"],
        "chave": item.get("chave"),
        "colunas": colunas,
        "linhas": linhas,
        "qtd_linhas": len(linhas),
        "tempo_s": round(tempo, 3),
        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    PASTA_EXPECTED.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
    return destino


def main() -> None:
    """Roda os SQLs do golden set e grava os JSONs esperados."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ids", nargs="*", help="ids a gerar (padrão: todos com SQL)")
    args = parser.parse_args()

    itens = [i for i in carregar_golden() if i["sql_esperado"].strip()]
    if args.ids:
        itens = [i for i in itens if i["id"] in set(args.ids)]

    conexao = abrir_conexao(carregar_config().db_path)
    try:
        for item in itens:
            colunas, linhas, tempo = executar(conexao, item["sql_esperado"])
            salvar(item, colunas, linhas, tempo)
            logger.info("%s  %7.2f s  %3d linhas  %s", item["id"], tempo, len(linhas), linhas[:3])
    finally:
        conexao.close()


if __name__ == "__main__":
    main()
