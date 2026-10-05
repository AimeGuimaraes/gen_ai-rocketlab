"""Leitura do dicionário de dados e geração do texto de esquema para o prompt."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from cinedata_agent.config import RAIZ_PROJETO

CAMINHO_SCHEMA = RAIZ_PROJETO / "docs" / "schema_docs.yaml"


def carregar_schema(caminho: Path | None = None) -> dict[str, Any]:
    """Lê o YAML do dicionário de dados e devolve um dicionário."""
    with open(caminho or CAMINHO_SCHEMA, encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)


def _formatar_coluna(nome: str, info: dict[str, Any]) -> str:
    """Formata uma coluna como '- nome: descrição [unidade]'."""
    unidade = info.get("moeda") or info.get("unidade")
    sufixo = f" [{unidade}]" if unidade else ""
    return f"- {nome}: {info['descricao']}{sufixo}"


@lru_cache(maxsize=4)
def gerar_texto_schema(caminho: Path | None = None) -> str:
    """Gera o texto compacto de esquema e regras de negócio para o prompt do agente."""
    schema = carregar_schema(caminho)
    todas_tabelas = {**schema["tabelas"], **schema.get("tabelas_auxiliares", {})}
    linhas: list[str] = ["## Convenções"]
    linhas += [f"- {item}" for item in schema["convencoes"]]

    linhas += ["", "## Tabelas"]
    for tabela, info in todas_tabelas.items():
        linhas += ["", f"### {tabela} (PK {info['chave_primaria']}): {info['descricao']}"]
        linhas += [_formatar_coluna(nome, col) for nome, col in info["colunas"].items()]

    linhas += ["", "## Ligações"]
    vistas: set[frozenset[str]] = set()
    for tabela, info in todas_tabelas.items():
        for ligacao in info["ligacoes"]:
            origem, destino = (parte.strip() for parte in ligacao.split("->"))
            par = frozenset({f"{tabela}.{origem}", destino.split()[0]})
            if par not in vistas:  # cada ligação aparece só uma vez, no primeiro sentido
                vistas.add(par)
                linhas.append(f"- {tabela}.{ligacao}")

    linhas += ["", "## Regras de negócio"]
    linhas += [f"- {regra['id']} {regra['titulo']}: {regra['regra']}" for regra in schema["regras_de_negocio"]]
    return "\n".join(linhas)


def contar_tokens_aprox(texto: str) -> int:
    """Estima a quantidade de tokens (cerca de 4 caracteres por token)."""
    return round(len(texto) / 4)
