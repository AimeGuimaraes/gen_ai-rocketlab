"""Cache de respostas em um SQLite próprio (.cache/cache.db), separado do cinerocket.db.

A chave combina a pergunta normalizada, o modelo (ou cadeia de fallback) e um hash do prompt de
sistema: mudar o prompt ou os modelos invalida as respostas antigas.
"""

import hashlib
import logging
import sqlite3
from datetime import UTC, datetime

from cinedata_agent.config import RAIZ_PROJETO
from cinedata_agent.guardrails import normalizar_texto

logger = logging.getLogger(__name__)

CAMINHO_CACHE = RAIZ_PROJETO / ".cache" / "cache.db"

_CRIAR_TABELA = """
CREATE TABLE IF NOT EXISTS respostas (
    chave TEXT PRIMARY KEY,
    pergunta TEXT NOT NULL,
    modelo TEXT NOT NULL,
    resposta_json TEXT NOT NULL,
    criado_em TEXT NOT NULL
)
"""


def _sha256(texto: str) -> str:
    """Hash SHA-256 do texto, em hexadecimal."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def chave_cache(pergunta: str, modelo: str, prompt_sistema: str) -> str:
    """Monta a chave do cache: pergunta normalizada + modelo + hash do prompt de sistema."""
    return _sha256(f"{normalizar_texto(pergunta)}\n{modelo}\n{_sha256(prompt_sistema)}")


def _agora() -> str:
    """Data e hora local com fuso, em ISO (ex.: 2026-10-04T15:30:00-03:00)."""
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _conectar() -> sqlite3.Connection:
    """Abre o banco do cache (criando a pasta e a tabela se preciso)."""
    CAMINHO_CACHE.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(CAMINHO_CACHE, timeout=5)
    conexao.execute(_CRIAR_TABELA)
    return conexao


def buscar(chave: str) -> str | None:
    """Devolve o JSON da resposta guardada para a chave, ou None se não houver (ou se o cache falhar)."""
    try:
        conexao = _conectar()
        try:
            linha = conexao.execute("SELECT resposta_json FROM respostas WHERE chave = ?", (chave,)).fetchone()
        finally:
            conexao.close()
    except sqlite3.Error as erro:
        logger.warning("Cache indisponível na leitura (%s); seguindo sem cache.", erro)
        return None
    return linha[0] if linha else None


def salvar(chave: str, pergunta: str, modelo: str, resposta_json: str) -> None:
    """Guarda a resposta completa (em JSON) com a data; falhas do cache só geram aviso no log."""
    try:
        conexao = _conectar()
        try:
            with conexao:
                conexao.execute(
                    "INSERT OR REPLACE INTO respostas (chave, pergunta, modelo, resposta_json, criado_em) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (chave, pergunta, modelo, resposta_json, _agora()),
                )
        finally:
            conexao.close()
    except sqlite3.Error as erro:
        logger.warning("Não foi possível gravar no cache (%s).", erro)
        return
    logger.info("Resposta guardada no cache.")
