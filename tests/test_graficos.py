"""Testes do gráfico automático (sem modelo)."""

from typing import Any

from cinedata_agent.graficos import Grafico, criar_figura, escolher_grafico


def _por_genero(n: int) -> list[dict[str, Any]]:
    """Resultado no formato 'quantidade de filmes por gênero'."""
    return [{"nome_genero": f"Gênero {i}", "qtd_filmes": 100 - i} for i in range(n)]


def test_texto_e_numero_vira_barras() -> None:
    linhas = _por_genero(19)
    assert escolher_grafico(["nome_genero", "qtd_filmes"], linhas) == Grafico("barras", "nome_genero", "qtd_filmes")


def test_ano_e_numero_vira_linha() -> None:
    linhas = [{"ano_lancamento": 2016 + i, "nota_media_imdb": 6.0 + i / 10, "qtd_filmes": 50} for i in range(8)]
    grafico = escolher_grafico(["ano_lancamento", "nota_media_imdb", "qtd_filmes"], linhas)
    assert grafico == Grafico("linha", "ano_lancamento", "nota_media_imdb")


def test_lista_de_filmes_com_ano_vira_barras() -> None:
    """Numa lista de filmes o ano é só atributo (pode repetir): barras pelo título."""
    linhas = [{"titulo": f"Filme {i}", "ano_lancamento": 2019, "receita_usd": 1e9 - i} for i in range(5)]
    grafico = escolher_grafico(["titulo", "ano_lancamento", "receita_usd"], linhas)
    assert grafico == Grafico("barras", "titulo", "receita_usd")


def test_fora_do_intervalo_de_linhas_nao_tem_grafico() -> None:
    assert escolher_grafico(["nome_genero", "qtd_filmes"], _por_genero(2)) is None
    assert escolher_grafico(["nome_genero", "qtd_filmes"], _por_genero(31)) is None
    assert escolher_grafico(["nome_genero", "qtd_filmes"], _por_genero(3)) is not None
    assert escolher_grafico(["nome_genero", "qtd_filmes"], _por_genero(30)) is not None


def test_sem_coluna_numerica_nao_tem_grafico() -> None:
    linhas = [{"titulo": f"Filme {i}", "autor": "Ana"} for i in range(5)]
    assert escolher_grafico(["titulo", "autor"], linhas) is None


def test_ignora_colunas_de_id() -> None:
    linhas = [{"id_filme": i, "titulo": f"Filme {i}"} for i in range(5)]
    assert escolher_grafico(["id_filme", "titulo"], linhas) is None


def test_criar_figura() -> None:
    linhas = [{"ano_lancamento": 2020 - i, "nota": 7.0 - i / 10} for i in range(4)]
    figura = criar_figura(Grafico("linha", "ano_lancamento", "nota"), linhas)
    assert list(figura.data[0].x) == [2017, 2018, 2019, 2020]  # ordenado pelo ano
    barras = criar_figura(Grafico("barras", "nome_genero", "qtd_filmes"), _por_genero(5))
    assert barras.data[0].orientation == "h"
