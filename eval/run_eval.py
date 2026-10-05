"""Avalia o agente com o golden set: pergunta, reexecuta o último SQL gerado e compara o resultado.

Uso:
    python eval/run_eval.py --dry-run          # só lista e estima as requisições (não chama o modelo)
    python eval/run_eval.py                    # todas as perguntas ainda não avaliadas (gasta cota)
    python eval/run_eval.py --ids q01-q07      # um lote (também aceita q01,q05 ou q01-q03,q09)
    python eval/run_eval.py --ids q05 --refazer --no-cache

Compara o RESULTADO da consulta (não o texto do SQL) com eval/expected/<id>.json. Cada pergunta é
salva em eval/resultados/<id>.json assim que termina; uma nova execução pula as já avaliadas
(--refazer força). Se a cota acabar, para de forma limpa e a próxima execução continua de onde
parou. O relatório é gerado em eval/report.md com todas as perguntas já avaliadas.
"""

import argparse
import html
import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "scripts"))

import httpx
import sqlglot
from build_expected import PASTA_EXPECTED, carregar_golden
from check_quota import consultar_cota
from pydantic_ai.exceptions import (
    FallbackExceptionGroup,
    ModelAPIError,
    UsageLimitExceeded,
)
from pydantic_ai.models import Model
from sqlglot import exp
from sqlglot.errors import ParseError

from cinedata_agent import cache
from cinedata_agent.agent import (
    MAX_REQUISICOES,
    ErroAgente,
    QuotaExceededError,
    RespostaAgente,
    _modelo_padrao,
    ask,
)
from cinedata_agent.db import ErroConsulta, inicializar_banco, run_query
from cinedata_agent.guardrails import normalizar_texto, verificar_pergunta
from cinedata_agent.prompts import montar_prompt_sistema

PASTA_RESULTADOS = RAIZ / "eval" / "resultados"
CAMINHO_RELATORIO = RAIZ / "eval" / "report.md"

REQUISICOES_TIPICAS = 2  # medido nas Etapas 6 e 7 (run_sql + resposta final)
COTA_MINIMA = 3  # não começa uma pergunta nova se restar menos que isso
TOLERANCIA = 0.01  # diferença numérica aceita
TEMPO_LIMITE_REEXECUCAO_S = 60.0  # a reexecução não precisa do limite curto do agente
COLUNAS_OPCIONAIS = {"id_filme", "ano_lancamento"}  # conferidas só se o agente trouxer
MAX_LINHAS_SALVAS = 50

# No script, print é a saída para o usuário; os passos internos vão para o logging.
logger = logging.getLogger("run_eval")


class ModelosIndisponiveis(Exception):
    """Todos os modelos estão fora do ar: a rodada para sem salvar a pergunta (tentar mais tarde)."""


@dataclass(frozen=True)
class Estimativa:
    """Gasto previsto de uma pergunta: típico, máximo e o motivo."""

    tipicas: int
    maximo: int
    motivo: str  # "guardrails", "cache" ou "modelo"


# --- Seleção das perguntas ---

def parse_ids(texto: str) -> list[str]:
    """Converte "q01,q02", "q01-q07" ou combinações ("q01-q03,q09") em ids, sem repetir."""
    ids: list[str] = []
    for parte in (p.strip() for p in texto.split(",")):
        if not parte:
            continue
        inicio, _, fim = parte.partition("-")
        try:
            numeros = range(_numero_id(inicio), _numero_id(fim or inicio) + 1)
        except ValueError as erro:
            raise ValueError(f"id inválido: {parte!r} (use q01, q01,q02 ou q01-q07)") from erro
        ids.extend(f"q{n:02d}" for n in numeros)
    return list(dict.fromkeys(ids))


def _numero_id(texto: str) -> int:
    """Número de um id ("q07" ou "7" -> 7)."""
    return int(texto.strip().lower().removeprefix("q"))


def selecionar(golden: list[dict[str, Any]], ids: list[str] | None) -> list[dict[str, Any]]:
    """Filtra o golden set pelos ids pedidos (na ordem do golden set)."""
    if not ids:
        return golden
    desconhecidos = set(ids) - {item["id"] for item in golden}
    if desconhecidos:
        raise ValueError(f"ids fora do golden set: {', '.join(sorted(desconhecidos))}")
    return [item for item in golden if item["id"] in set(ids)]


# --- Comparador ---

def _chaves_item(item: dict[str, Any]) -> list[str]:
    """Coluna(s) da chave do item, sempre como lista."""
    chave = item["chave"]
    return chave if isinstance(chave, list) else [chave]


def normalizar_valor(valor: Any) -> str:
    """Texto comparável: minúsculas, sem acentos, 2016.0 = 2016 e sem o sufixo " (ano)" do título."""
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return re.sub(r"\s*\(\d{4}\)$", "", normalizar_texto(str(valor)))


def _numero(valor: Any) -> float | None:
    """Valor como float (aceita texto numérico) ou None se não for número."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, int | float):
        return float(valor)
    try:
        return float(str(valor).strip())
    except ValueError:
        return None


def _numerica(valores: list[Any]) -> bool:
    """Diz se a coluna esperada é numérica (int/float), ignorando nulos."""
    presentes = [v for v in valores if v is not None]
    return bool(presentes) and all(isinstance(v, int | float) and not isinstance(v, bool) for v in presentes)


def _iguais(a: Any, b: Any) -> bool:
    """Compara dois valores numéricos com a tolerância (nulo só é igual a nulo)."""
    if a is None or b is None:
        return a is None and b is None
    na, nb = _numero(a), _numero(b)
    return na is not None and nb is not None and abs(na - nb) <= TOLERANCIA + 1e-9


def achar_coluna(
    nome: str, esperados: list[Any], colunas: dict[str, list[Any]], ignorar: set[str] | None = None
) -> str | None:
    """Coluna do agente com o mesmo nome ou, se não houver, a cujos valores mais coincidem com os esperados."""
    ignorar = ignorar or set()
    if nome in colunas and nome not in ignorar:
        return nome
    alvo = Counter(normalizar_valor(v) for v in esperados)
    melhor, melhor_qtd = None, 0
    for coluna, valores in colunas.items():
        if coluna in ignorar:
            continue
        qtd = sum((Counter(normalizar_valor(v) for v in valores) & alvo).values())
        if qtd > melhor_qtd:
            melhor, melhor_qtd = coluna, qtd
    return melhor


def _por_chave(chaves: list[tuple[str, ...]], valores: list[Any]) -> dict[tuple[str, ...], list[Any]]:
    """Agrupa os valores pela chave da linha, em ordem numérica (para chaves repetidas)."""
    grupos: dict[tuple[str, ...], list[Any]] = defaultdict(list)
    for chave, valor in zip(chaves, valores, strict=True):
        grupos[chave].append(valor)
    for lista in grupos.values():
        lista.sort(key=lambda v: (_numero(v) is None, _numero(v) or 0.0))
    return grupos


def _coluna_numerica_igual(
    nome: str,
    esperados: dict[tuple[str, ...], list[Any]],
    colunas: dict[str, list[Any]],
    chaves_agente: list[tuple[str, ...]],
    ignorar: set[str],
) -> str | None:
    """Coluna do agente com os mesmos valores numéricos, linha a linha pela chave (o nome não importa)."""
    candidatas = sorted((c for c in colunas if c not in ignorar), key=lambda c: c != nome)
    for coluna in candidatas:
        obtidos = _por_chave(chaves_agente, colunas[coluna])
        if all(
            len(obtidos.get(chave, [])) == len(valores)
            and all(_iguais(e, o) for e, o in zip(valores, obtidos[chave], strict=True))
            for chave, valores in esperados.items()
        ):
            return coluna
    return None


def _formatar_chaves(
    chaves: list[tuple[str, ...]] | Counter[tuple[str, ...]], originais: dict[tuple[str, ...], str], limite: int = 5
) -> str:
    """Lista curta de chaves para as mensagens, com os valores como vieram (sem normalizar)."""
    lista = list(chaves)
    textos = [originais.get(c, " / ".join(c)) for c in lista[:limite]]
    resto = len(lista) - limite
    return ", ".join(textos) + (f" (+{resto})" if resto > 0 else "")


def _chaves_linhas(colunas: list[list[Any]], originais: dict[tuple[str, ...], str]) -> list[tuple[str, ...]]:
    """Chave normalizada de cada linha; guarda em `originais` o texto original de cada chave."""
    chaves = []
    for valores in zip(*colunas, strict=True):
        chave = tuple(normalizar_valor(v) for v in valores)
        originais.setdefault(chave, " / ".join(str(v) for v in valores))
        chaves.append(chave)
    return chaves


def comparar(
    item: dict[str, Any],
    esperado: dict[str, Any] | None,
    colunas: list[str],
    linhas: list[dict[str, Any]],
    sql_executados: list[str],
    recusada: bool = False,
) -> tuple[bool, str]:
    """Compara a resposta do agente com o esperado conforme o tipo_checagem; devolve (acertou, detalhe)."""
    tipo = item["tipo_checagem"]
    if tipo == "recusa":
        if recusada or not sql_executados:
            return True, "recusou sem executar SQL"
        return False, f"executou {len(sql_executados)} SQL em vez de recusar"
    if recusada or not sql_executados:
        return False, "o agente não executou SQL" + (" (pergunta recusada)" if recusada else "")
    if esperado is None:
        return False, "resultado esperado ausente (rode eval/build_expected.py)"
    if not linhas:
        return False, "a última consulta não devolveu linhas"

    obtidas = {c: [linha.get(c) for linha in linhas] for c in colunas}
    esperadas = {c: [linha[i] for linha in esperado["linhas"]] for i, c in enumerate(esperado["colunas"])}
    nomes_chave = _chaves_item(item)
    mapa: dict[str, str] = {}
    for nome in nomes_chave:
        coluna = achar_coluna(nome, esperadas[nome], obtidas, ignorar=set(mapa.values()))
        if coluna is None:
            return False, f"nenhuma coluna do resultado corresponde à chave '{nome}'"
        mapa[nome] = coluna
    originais: dict[tuple[str, ...], str] = {}
    chaves_esp = _chaves_linhas([esperadas[n] for n in nomes_chave], originais)
    chaves_obt = _chaves_linhas([obtidas[mapa[n]] for n in nomes_chave], originais)
    descricao_chave = ", ".join(f"{n}→{c}" if n != c else n for n, c in mapa.items())

    if tipo == "top_n_contem":
        top = chaves_esp[: item["top_n"]]
        faltando = [c for c in top if c not in set(chaves_obt)]
        if faltando:
            return False, (
                f"faltam entre as {item['top_n']} primeiras esperadas: {_formatar_chaves(faltando, originais)}"
            )
        return True, f"as {item['top_n']} primeiras esperadas aparecem (chave: {descricao_chave})"

    contagem_esp, contagem_obt = Counter(chaves_esp), Counter(chaves_obt)
    if contagem_esp != contagem_obt:
        partes = []
        if faltam := contagem_esp - contagem_obt:
            partes.append(f"faltam {_formatar_chaves(faltam, originais)}")
        if sobram := contagem_obt - contagem_esp:
            partes.append(f"sobram {_formatar_chaves(sobram, originais)}")
        return False, f"linhas diferentes ({len(chaves_obt)} obtidas, {len(chaves_esp)} esperadas): " + "; ".join(partes)

    usadas = set(mapa.values())
    opcionais = COLUNAS_OPCIONAIS | colunas_so_filtro(item.get("sql_esperado", ""))
    conferidas, ausentes = [], []
    for nome, valores in esperadas.items():
        if nome in nomes_chave or not _numerica(valores):
            continue
        coluna = _coluna_numerica_igual(nome, _por_chave(chaves_esp, valores), obtidas, chaves_obt, usadas)
        if coluna is not None:
            conferidas.append(nome if coluna == nome else f"{nome}→{coluna}")
        elif nome in opcionais:
            ausentes.append(nome)
        else:
            return False, f"nenhuma coluna do resultado bate com '{nome}' (tolerância de {TOLERANCIA})"
    detalhe = f"mesmas linhas (chave: {descricao_chave}); valores conferidos: {', '.join(conferidas) or '-'}"
    return True, detalhe + (f"; opcionais ausentes: {', '.join(ausentes)}" if ausentes else "")


def colunas_so_filtro(sql: str) -> set[str]:
    """Colunas do SELECT esperado usadas só como filtro (o agente não precisa mostrá-las).

    Critério: coluna direta da base que aparece no WHERE/HAVING, mas não no ORDER BY nem em outra
    coluna calculada do SELECT. Ex.: na q04, qtd_tmdb só entra no WHERE (qtd_tmdb >= 100), e a
    pergunta é sobre popularidade. Colunas calculadas (AVG, ROUND...) e de ordenação seguem obrigatórias.
    """
    if not sql.strip():
        return set()
    try:
        arvore = sqlglot.parse_one(sql, read="sqlite")
    except ParseError:
        return set()
    if not isinstance(arvore, exp.Select):
        return set()

    def nomes(no: exp.Expression | None) -> set[str]:
        return {c.name for c in no.find_all(exp.Column)} if no is not None else set()

    filtros = nomes(arvore.args.get("where")) | nomes(arvore.args.get("having"))
    ordenacao = nomes(arvore.args.get("order"))
    calculadas = set().union(*(nomes(p) for p in arvore.expressions if not isinstance(p.unalias(), exp.Column)))
    resultado = set()
    for projecao in arvore.expressions:
        coluna = projecao.unalias()
        if isinstance(coluna, exp.Column) and coluna.name in filtros and coluna.name not in ordenacao | calculadas:
            resultado.add(projecao.alias_or_name)
    return resultado


# --- Estimativa e cota ---

def estimar(item: dict[str, Any], modelo: Model, usar_cache: bool = True) -> Estimativa:
    """Prevê as requisições de uma pergunta sem chamar o modelo (guardrails e cache custam 0)."""
    if verificar_pergunta(item["pergunta"]) is not None:
        return Estimativa(0, 0, "guardrails")
    if usar_cache and _resposta_do_cache(item["pergunta"], modelo) is not None:
        return Estimativa(0, 0, "cache")
    return Estimativa(REQUISICOES_TIPICAS, MAX_REQUISICOES, "modelo")


def _resposta_do_cache(pergunta: str, modelo: Model) -> RespostaAgente | None:
    """Resposta original guardada no cache (com as requisições e o tempo da primeira vez)."""
    guardada = cache.buscar(cache.chave_cache(pergunta, modelo.model_name, montar_prompt_sistema()))
    if guardada is None:
        return None
    try:
        return RespostaAgente.model_validate_json(guardada)
    except ValueError:
        return None


def cota_restante() -> int | None:
    """Requisições gratuitas restantes hoje, ou None se não foi possível consultar."""
    try:
        return int(consultar_cota()["remaining"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as erro:  # rede, chave ou formato
        logger.warning("Não consegui consultar a cota (%s); seguindo sem a checagem.", erro)
        return None


# --- Execução ---

def avaliar_pergunta(item: dict[str, Any], modelo: Model, usar_cache: bool = True) -> dict[str, Any]:
    """Pergunta ao agente, reexecuta o último SQL e compara com o esperado; devolve o registro.

    Levanta QuotaExceededError (cota acabou) e ModelosIndisponiveis (todos fora do ar).
    """
    inicio = time.perf_counter()
    registro: dict[str, Any] = {
        "id": item["id"],
        "categoria": item["categoria"],
        "pergunta": item["pergunta"],
        "tipo_checagem": item["tipo_checagem"],
        "informativa": bool(item.get("informativa", False)),
        "sql_esperado": item["sql_esperado"],
        "avaliado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "aviso": None,
        "erro": None,
    }
    try:
        resposta = ask(item["pergunta"], modelo=modelo, usar_cache=usar_cache)
    except ErroAgente as erro:
        if isinstance(erro.__cause__, FallbackExceptionGroup):
            raise ModelosIndisponiveis(str(erro)) from erro
        # Estourar o limite significa ter gasto todas as requisições; nos outros erros o gasto é desconhecido.
        gastas = MAX_REQUISICOES if isinstance(erro.__cause__, UsageLimitExceeded) else None
        return registro | {
            "acertou": False, "detalhe": f"erro do agente: {erro}", "erro": str(erro),
            "resposta": None, "sql_gerado": None, "sql_executados": [], "colunas": [], "linhas": [],
            "modelo": None, "requisicoes": gastas, "tempo_ms": round((time.perf_counter() - inicio) * 1000),
            "cache": False, "recusada": False,
        }

    requisicoes, tempo_ms = resposta.requisicoes, resposta.tempo_ms
    if resposta.cache and (original := _resposta_do_cache(item["pergunta"], modelo)) is not None:
        requisicoes, tempo_ms = original.requisicoes, original.tempo_ms  # custo da primeira vez

    colunas, linhas = resposta.colunas, resposta.linhas
    if resposta.sql_executados:
        try:
            resultado = run_query(resposta.sql_executados[-1], tempo_limite_s=TEMPO_LIMITE_REEXECUCAO_S)
            colunas, linhas = resultado.colunas, resultado.linhas
        except ErroConsulta as erro:
            registro["aviso"] = f"reexecução falhou ({erro}); usado o resultado guardado na resposta"
            logger.warning("%s: %s", item["id"], registro["aviso"])

    esperado = _carregar_esperado(item["id"]) if item["tipo_checagem"] != "recusa" else None
    acertou, detalhe = comparar(item, esperado, colunas, linhas, resposta.sql_executados, resposta.recusada)
    return registro | {
        "acertou": acertou,
        "detalhe": detalhe,
        "resposta": resposta.resposta,
        "sql_gerado": resposta.sql_executados[-1] if resposta.sql_executados else None,
        "sql_executados": resposta.sql_executados,
        "colunas": colunas,
        "linhas": linhas[:MAX_LINHAS_SALVAS],
        "modelo": resposta.modelo,
        "requisicoes": requisicoes,
        "tempo_ms": tempo_ms,
        "cache": resposta.cache,
        "recusada": resposta.recusada,
    }


def _carregar_esperado(id_pergunta: str) -> dict[str, Any] | None:
    """Lê eval/expected/<id>.json, ou None se não existir."""
    caminho = PASTA_EXPECTED / f"{id_pergunta}.json"
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


def salvar_registro(registro: dict[str, Any], pasta: Path | None = None) -> Path:
    """Grava o registro da pergunta em eval/resultados/<id>.json."""
    pasta = pasta or PASTA_RESULTADOS
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{registro['id']}.json"
    destino.write_text(json.dumps(registro, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return destino


def carregar_registros(pasta: Path | None = None) -> dict[str, dict[str, Any]]:
    """Lê todos os registros já avaliados, por id."""
    pasta = pasta or PASTA_RESULTADOS
    if not pasta.exists():
        return {}
    registros = (json.loads(c.read_text(encoding="utf-8")) for c in sorted(pasta.glob("q*.json")))
    return {r["id"]: r for r in registros}


def rodar(
    itens: list[dict[str, Any]], modelo: Model, usar_cache: bool = True, pasta: Path | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """Avalia as perguntas em ordem, salvando cada uma; devolve os registros e o motivo da parada (ou None)."""
    registros: list[dict[str, Any]] = []
    for item in itens:
        if estimar(item, modelo, usar_cache).motivo == "modelo":
            restante = cota_restante()
            if restante is not None and restante < COTA_MINIMA:
                return registros, f"restam só {restante} requisições hoje (mínimo para seguir: {COTA_MINIMA})"
        logger.info("Avaliando %s: %s", item["id"], item["pergunta"])
        try:
            registro = avaliar_pergunta(item, modelo, usar_cache)
        except QuotaExceededError as erro:
            return registros, f"cota esgotada em {item['id']} ({erro})"
        except ModelosIndisponiveis as erro:
            return registros, f"modelos indisponíveis em {item['id']} ({erro})"
        except ModelAPIError as erro:  # ex.: 502 do provedor; a pergunta não é salva e pode ser refeita
            logger.error("Erro do modelo em %s: %s", item["id"], erro)
            return registros, f"erro do modelo em {item['id']} ({erro})"
        salvar_registro(registro, pasta)
        registros.append(registro)
        logger.info("%s: %s (%s)", item["id"], "acertou" if registro["acertou"] else "errou", registro["detalhe"])
    return registros, None


# --- Relatório ---

def _pct(acertos: int, total: int) -> str:
    """Percentual com vírgula (ex.: 85,0%)."""
    return f"{acertos / total * 100:.1f}%".replace(".", ",") if total else "-"


def _decimal(valor: float, casas: int = 1) -> str:
    """Número com vírgula decimal."""
    return f"{valor:.{casas}f}".replace(".", ",")


def _status(registro: dict[str, Any]) -> str:
    """Ícone do resultado (informativa tem ícone próprio)."""
    icone = "✅" if registro["acertou"] else "❌"
    return f"ℹ️ {icone} (informativa)" if registro["informativa"] else icone


def _celula(texto: Any) -> str:
    """Texto seguro para uma célula de tabela markdown."""
    return str(texto if texto is not None else "-").replace("|", "\\|").replace("\n", " ")


def gerar_relatorio(registros: dict[str, dict[str, Any]], golden: list[dict[str, Any]]) -> str:
    """Monta o eval/report.md: tabela por pergunta, acerto por categoria e total, requisições e modelos."""
    avaliados = [registros[item["id"]] for item in golden if item["id"] in registros]
    pendentes = [item["id"] for item in golden if item["id"] not in registros]
    contam = [r for r in avaliados if not r["informativa"]]
    acertos = sum(r["acertou"] for r in contam)
    com_req = [r["requisicoes"] for r in avaliados if r["requisicoes"] is not None]
    com_modelo = [n for n in com_req if n > 0]
    modelos = Counter(r["modelo"] for r in avaliados if r["modelo"])

    linhas = [
        "# Avaliação do CineData Agent",
        "",
        (
            f"Gerado em {datetime.now().astimezone().isoformat(timespec='seconds')} por "
            f"`python eval/run_eval.py`. Perguntas avaliadas: {len(avaliados)} de {len(golden)}."
        ),
        "",
        (
            "A avaliação compara o **resultado** do último SQL gerado (reexecutado no banco) com "
            "`eval/expected/<id>.json`, não o texto do SQL. Respostas vindas do cache mostram as "
            "requisições e o tempo da primeira vez em que foram geradas."
        ),
        "",
        "## Resumo",
        "",
        f"- **Acerto total:** {acertos}/{len(contam)} ({_pct(acertos, len(contam))}), sem as perguntas informativas.",
        (
            f"- **Média de requisições por pergunta:** {_decimal(sum(com_req) / len(com_req)) if com_req else '-'} "
            f"(todas as {len(com_req)} perguntas com contagem, recusas dos guardrails = 0); "
            f"{_decimal(sum(com_modelo) / len(com_modelo)) if com_modelo else '-'} "
            f"entre as {len(com_modelo)} que chamaram o modelo."
        ),
        "- **Modelos usados:** "
        + (", ".join(f"`{m}` ({n})" for m, n in modelos.most_common()) if modelos else "-"),
    ]
    if pendentes:
        linhas.append(f"- **Ainda não avaliadas:** {', '.join(pendentes)}.")

    linhas += ["", "## Acerto por categoria", "", "| Categoria | Acertos | Total | % |", "|---|---:|---:|---:|"]
    por_categoria: dict[str, list[bool]] = defaultdict(list)
    for r in contam:
        por_categoria[r["categoria"]].append(r["acertou"])
    for categoria, resultados in por_categoria.items():
        linhas.append(f"| {categoria} | {sum(resultados)} | {len(resultados)} | {_pct(sum(resultados), len(resultados))} |")
    linhas.append(f"| **Total** | **{acertos}** | **{len(contam)}** | **{_pct(acertos, len(contam))}** |")

    linhas += [
        "",
        "## Por pergunta",
        "",
        "| id | Categoria | Checagem | Acertou | Requisições | Tempo (s) | Modelo | Cache | Detalhe |",
        "|---|---|---|:---:|---:|---:|---|:---:|---|",
    ]
    for r in avaliados:
        tempo = _decimal(r["tempo_ms"] / 1000) if r["tempo_ms"] is not None else "-"
        linhas.append(
            f"| {r['id']} | {r['categoria']} | {r['tipo_checagem']} | {_status(r)} | {_celula(r['requisicoes'])} "
            f"| {tempo} | {_celula(r['modelo'])} | {'sim' if r['cache'] else 'não'} | {_celula(r['detalhe'])} |"
        )

    erradas = [r for r in avaliados if not r["acertou"]]
    if erradas:
        linhas += ["", "## Perguntas erradas: SQL gerado × esperado"]
    for r in erradas:
        linhas += [
            "",
            f"### {r['id']} — {r['pergunta']}",
            "",
            f"**Motivo:** {r['detalhe']}" + (" (informativa, fora da taxa de acerto)" if r["informativa"] else ""),
        ]
        if r.get("aviso"):
            linhas.append(f"\n**Aviso:** {r['aviso']}")
        linhas += [
            "",
            "<table><tr><th>SQL gerado</th><th>SQL esperado</th></tr><tr>",
            f"<td><pre>{html.escape(r['sql_gerado'] or '(nenhum SQL executado)')}</pre></td>",
            f"<td><pre>{html.escape(r['sql_esperado'].strip() or '(recusar, sem SQL)')}</pre></td>",
            "</tr></table>",
        ]
    return "\n".join(linhas) + "\n"


def escrever_relatorio(golden: list[dict[str, Any]], pasta: Path | None = None, destino: Path | None = None) -> Path:
    """Gera o relatório com todos os registros salvos e grava em eval/report.md."""
    destino = destino or CAMINHO_RELATORIO
    destino.write_text(gerar_relatorio(carregar_registros(pasta), golden), encoding="utf-8")
    return destino


# --- CLI ---

def _legivel(caminho: Path) -> str:
    """Caminho relativo à raiz do projeto, quando possível."""
    return str(caminho.relative_to(RAIZ)) if caminho.is_relative_to(RAIZ) else str(caminho)


def _tipo_ids(texto: str) -> list[str]:
    """Conversor do argparse para --ids."""
    try:
        return parse_ids(texto)
    except ValueError as erro:
        raise argparse.ArgumentTypeError(str(erro)) from erro


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada; devolve 0 se terminou, 1 em erro de uso e 2 se parou antes do fim."""
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ids", type=_tipo_ids, help="ex.: q01,q02 ou q01-q07 (padrão: todas)")
    parser.add_argument("--dry-run", action="store_true", help="só lista e estima as requisições")
    parser.add_argument("--no-cache", action="store_true", help="ignora o cache e sempre chama o modelo")
    parser.add_argument("--refazer", action="store_true", help="reavalia perguntas já salvas em eval/resultados/")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # sem uma linha por requisição HTTP
    usar_cache = not args.no_cache

    golden = carregar_golden()
    try:
        itens = selecionar(golden, args.ids)
    except ValueError as erro:
        print(erro)
        return 1
    ja_avaliadas = set() if args.refazer else set(carregar_registros())
    pendentes = [item for item in itens if item["id"] not in ja_avaliadas]
    puladas = [item["id"] for item in itens if item["id"] in ja_avaliadas]

    modelo = _modelo_padrao()
    estimativas = {item["id"]: estimar(item, modelo, usar_cache) for item in pendentes}
    tipicas = sum(e.tipicas for e in estimativas.values())
    maximo = sum(e.maximo for e in estimativas.values())

    print(f"{'Modo dry-run: nada será executado.' if args.dry_run else 'Rodada de avaliação.'}")
    if puladas:
        print(f"Já avaliadas (puladas; use --refazer para repetir): {', '.join(puladas)}")
    print(f"\n{'id':<4}  {'categoria':<20}  {'origem':<10}  req.  pergunta")
    for item in pendentes:
        e = estimativas[item["id"]]
        print(f"{item['id']:<4}  {item['categoria']:<20}  {e.motivo:<10}  {e.tipicas:>4}  {item['pergunta'][:60]}")
    print(
        f"\n{len(pendentes)} perguntas; {sum(e.motivo == 'modelo' for e in estimativas.values())} chamam o modelo. "
        f"Estimativa: ~{tipicas} requisições (típico de {REQUISICOES_TIPICAS} por pergunta; "
        f"máximo {maximo} se todas usarem o limite de {MAX_REQUISICOES})."
    )

    if tipicas > 0:
        restante = cota_restante()
        if restante is not None:
            print(f"Cota restante hoje: {restante} requisições.")
            if restante < tipicas:
                print(
                    "Aviso: a cota pode não bastar. A rodada segue em ordem, para quando restarem menos de "
                    f"{COTA_MINIMA} e uma nova execução continua de onde parou."
                )
    if args.dry_run or not pendentes:
        if not args.dry_run:
            print(f"Nada a avaliar; relatório atualizado em {_legivel(escrever_relatorio(golden))}.")
        return 0

    inicializar_banco()  # uma vez só, para todas as perguntas
    registros, parada = rodar(pendentes, modelo, usar_cache)
    destino = escrever_relatorio(golden)
    gastas = sum(r["requisicoes"] or 0 for r in registros if not r["cache"])
    acertos = sum(r["acertou"] for r in registros if not r["informativa"])
    contam = sum(not r["informativa"] for r in registros)
    print(f"\nAvaliadas nesta rodada: {len(registros)} ({acertos}/{contam} acertos); requisições gastas: ~{gastas}.")
    print(f"Relatório: {_legivel(destino)}")
    if parada:
        print(f"Rodada interrompida: {parada}. Rode de novo para continuar de onde parou.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
