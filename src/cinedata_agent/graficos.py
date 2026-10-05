"""Gráfico automático a partir do resultado de uma consulta, sem chamar o modelo.

Regras: só com 3 a 30 linhas; ano + coluna numérica -> linha; texto + coluna numérica -> barras.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd
import plotly.express as px
from plotly.graph_objects import Figure

logger = logging.getLogger(__name__)

MIN_LINHAS = 3
MAX_LINHAS = 30

_NOME_ANO = re.compile(r"(^|_)(ano|year)(_|$)")
_PREFIXOS_ID = ("id_", "sk_")


@dataclass(frozen=True)
class Grafico:
    """Tipo de gráfico e colunas dos eixos."""

    tipo: Literal["barras", "linha"]
    x: str
    y: str


def _valores(coluna: str, linhas: list[dict[str, Any]]) -> list[Any]:
    """Valores não nulos da coluna."""
    return [linha.get(coluna) for linha in linhas if linha.get(coluna) is not None]


def _numerica(valores: list[Any]) -> bool:
    """Todos os valores são números (bool não conta)."""
    return bool(valores) and all(isinstance(v, int | float) and not isinstance(v, bool) for v in valores)


def _texto(valores: list[Any]) -> bool:
    """Todos os valores são texto."""
    return bool(valores) and all(isinstance(v, str) for v in valores)


def _ano(coluna: str, valores: list[Any]) -> bool:
    """Coluna de ano: nome com 'ano'/'year' e valores inteiros entre 1900 e 2100."""
    return (
        bool(_NOME_ANO.search(coluna.lower()))
        and _numerica(valores)
        and all(float(v).is_integer() and 1900 <= v <= 2100 for v in valores)
    )


def _serie_por_ano(coluna: str, valores: list[Any], total_linhas: int) -> bool:
    """Ano que agrupa o resultado (um ano por linha), como em 'nota média por ano'.

    Numa lista de filmes o ano se repete e é só um atributo; aí o gráfico é de barras.
    """
    return _ano(coluna, valores) and len(valores) == total_linhas and len(set(valores)) == total_linhas


def escolher_grafico(colunas: list[str], linhas: list[dict[str, Any]]) -> Grafico | None:
    """Escolhe o gráfico para o resultado, ou None se nenhum fizer sentido."""
    if not MIN_LINHAS <= len(linhas) <= MAX_LINHAS:
        return None
    uteis = [c for c in colunas if not c.lower().startswith(_PREFIXOS_ID)]
    valores = {c: _valores(c, linhas) for c in uteis}
    anos = [c for c in uteis if _ano(c, valores[c])]
    numericas = [c for c in uteis if c not in anos and _numerica(valores[c])]
    textos = [c for c in uteis if _texto(valores[c])]
    if not numericas:
        return None
    series = [c for c in anos if _serie_por_ano(c, valores[c], len(linhas))]
    if series and not textos:
        return Grafico("linha", series[0], numericas[0])
    if textos:
        return Grafico("barras", textos[0], numericas[0])
    return None


def criar_figura(grafico: Grafico, linhas: list[dict[str, Any]]) -> Figure:
    """Monta a figura do Plotly: linha ordenada pelo ano ou barras horizontais na ordem do resultado."""
    dados = pd.DataFrame(linhas, columns=[grafico.x, grafico.y]).dropna()
    if grafico.tipo == "linha":
        figura = px.line(dados.sort_values(grafico.x), x=grafico.x, y=grafico.y, markers=True)
        figura.update_xaxes(dtick=1)
        altura = 360
    else:
        figura = px.bar(dados, x=grafico.y, y=grafico.x, orientation="h")
        figura.update_yaxes(autorange="reversed", type="category")  # primeiro do ranking no topo
        altura = max(320, 28 * len(dados))
    figura.update_layout(margin={"l": 10, "r": 10, "t": 30, "b": 10}, height=altura)
    return figura
