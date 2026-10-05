"""Guardrails verificados antes de chamar o modelo (não gastam cota).

Recusam perguntas vazias ou longas demais, pedidos de alteração de dados e padrões comuns de
prompt injection. A validação de SQL do db.py continua sendo a barreira final.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

MAX_CARACTERES = 500

MotivoRecusa = Literal["vazia", "longa", "alteracao", "injecao"]

MENSAGEM_VAZIA = "Escreva uma pergunta sobre o catálogo de filmes da CineData."
MENSAGEM_ALTERACAO = (
    "Não posso alterar dados: o CineData Agent tem acesso somente leitura ao catálogo de filmes. "
    'Posso ajudar com consultas, por exemplo: "Quais são os 10 filmes com maior receita?".'
)
MENSAGEM_INJECAO = (
    "Não posso mudar minhas instruções, trocar de papel nem revelar como fui configurado. "
    "Posso ajudar com perguntas sobre o catálogo de filmes da CineData."
)


@dataclass(frozen=True)
class Recusa:
    """Pergunta recusada pelos guardrails, com o motivo e a mensagem para o usuário."""

    motivo: MotivoRecusa
    mensagem: str


def normalizar_texto(texto: str) -> str:
    """Deixa o texto em minúsculas, sem acentos e sem espaços extras."""
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    sem_acentos = "".join(c for c in decomposto if not unicodedata.combining(c))
    return " ".join(sem_acentos.split())


# --- Padrões (aplicados ao texto normalizado) ---

# Até N palavras entre o verbo e o alvo ("apague a tabela", "apague todas as linhas da tabela").
_ATE_4_PALAVRAS = r"(?:\W+\w+){0,4}?\W+"
_ATE_3_PALAVRAS = r"(?:\W+\w+){0,3}?\W+"

_NOMES_TABELAS = r"(?:dim|fact|bridge|aux)_\w+|movie_reviews|cinerocket(?:\.db)?"
_BASE = r"banco|bancos|base|bases|database|esquema|schema"

_PADROES_INJECAO = [
    (
        rf"\b(?:ignor|esquec|desconsider|desobedec|desprez)\w*{_ATE_3_PALAVRAS}"
        r"(?:instruc|regra|prompt|orientac|diretriz|comando)\w*"
    ),
    r"\b(?:voce agora e|agora voce e|a partir de agora,? voce|finja (?:ser|que)|faca de conta que voce)\b",
    r"\b(?:prompt|instrucoes) (?:de|do) sistema\b|\bsystem prompt\b|\bseu prompt\b",
    r"\bignore (?:all |the |any |your )?(?:previous |prior |above )?(?:instructions|rules)\b",
    r"\byou are now\b|\bdeveloper mode\b|\bjailbreak\b",
]

_PADROES_ALTERACAO = [
    # SQL explícito.
    r"\b(?:drop|truncate)\s+(?:table|database|view|index|schema)\b",
    r"\bdelete\s+from\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b",
    r"\b(?:alter|create)\s+(?:table|view|index|database|schema)\b",
    # Verbos destrutivos: tabela, coluna e registro já bastam como alvo.
    (
        r"\b(?:apague|apagar|apaga|delete|deletar|deleta|limpe|limpar|limpa|zere|zerar|zera|drop|truncate)\b"
        rf"{_ATE_4_PALAVRAS}(?:tabelas?|colunas?|registros?|linhas?|{_BASE}|{_NOMES_TABELAS})\b"
    ),
    # Excluir/remover também servem como filtro ("exclua os registros sem receita"): só estruturas.
    (
        r"\b(?:exclua|excluir|exclui|remova|remover|remove)\b"
        rf"{_ATE_4_PALAVRAS}(?:tabelas?|colunas?|{_BASE}|{_NOMES_TABELAS})\b"
    ),
    # Verbos de modificação: "altere a tabela para mostrar em R$" é formato da resposta, então
    # tabela ou coluna só contam com nome da base (dim_movies, receita_usd).
    (
        r"\b(?:atualize|atualizar|atualiza|altere|alterar|altera|modifique|modificar|modifica|edite|editar|"
        r"edita|mude|mudar|insira|inserir|insere|update|insert)\b"
        rf"{_ATE_4_PALAVRAS}(?:registros?|{_BASE}|{_NOMES_TABELAS}|(?:tabelas?|colunas?)\W+\w+_\w+)\b"
    ),
    # Criação: "crie uma tabela com os 10 filmes" é formato da resposta; só recusa se o alvo for a base.
    (
        r"\b(?:crie|criar|cria|monte|montar|gere|gerar|adicione|adicionar|acrescente|acrescentar)\b"
        rf"{_ATE_4_PALAVRAS}(?:tabelas?|colunas?|registros?|indices?|views?)\b.{{0,40}}?"
        rf"(?:\b(?:no|na|nos|nas|em|do|da)\s+(?:{_BASE})\b|\b(?:{_NOMES_TABELAS})\b)"
    ),
]

_REGEX_INJECAO = [re.compile(p) for p in _PADROES_INJECAO]
_REGEX_ALTERACAO = [re.compile(p) for p in _PADROES_ALTERACAO]


def verificar_pergunta(pergunta: str) -> Recusa | None:
    """Devolve uma Recusa se a pergunta não deve chegar ao modelo, ou None se ela pode seguir."""
    recusa = _verificar(pergunta)
    if recusa is not None:
        logger.info("Pergunta recusada pelos guardrails (%s).", recusa.motivo)
    return recusa


def _verificar(pergunta: str) -> Recusa | None:
    """Aplica as regras em ordem: vazia, longa, prompt injection e alteração de dados."""
    texto = pergunta.strip()
    if not texto:
        return Recusa("vazia", MENSAGEM_VAZIA)
    if len(texto) > MAX_CARACTERES:
        return Recusa(
            "longa",
            f"A pergunta tem {len(texto)} caracteres e o limite é {MAX_CARACTERES}. Tente resumi-la.",
        )
    normalizado = normalizar_texto(texto)
    if any(regex.search(normalizado) for regex in _REGEX_INJECAO):
        return Recusa("injecao", MENSAGEM_INJECAO)
    if any(regex.search(normalizado) for regex in _REGEX_ALTERACAO):
        return Recusa("alteracao", MENSAGEM_ALTERACAO)
    return None
