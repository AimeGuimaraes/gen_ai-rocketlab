"""Configurações do agente lidas do arquivo .env."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

RAIZ_PROJETO = Path(__file__).resolve().parents[2]

PADRAO_BASE_URL = "https://openrouter.ai/api/v1"
PADRAO_MODELO = "nvidia/nemotron-3.5-lightning:free"
PADRAO_FALLBACK = "google/gemma-4-26b-a4b-it:free,openrouter/free"
PADRAO_DB_PATH = "cinerocket.db"


@dataclass(frozen=True)
class Config:
    """Configurações do agente (modelo, chave e banco)."""

    base_url: str
    api_key: str
    modelo: str
    modelos_fallback: tuple[str, ...]
    db_path: Path

    def __repr__(self) -> str:
        """Representação sem expor a chave da API."""
        return (
            f"Config(base_url={self.base_url!r}, modelo={self.modelo!r}, "
            f"modelos_fallback={self.modelos_fallback!r}, db_path={str(self.db_path)!r})"
        )


def _separar_lista(valor: str) -> tuple[str, ...]:
    """Separa uma lista por vírgulas, ignorando espaços e itens vazios."""
    return tuple(item.strip() for item in valor.split(",") if item.strip())


def _resolver_caminho(valor: str) -> Path:
    """Resolve caminhos relativos a partir da raiz do projeto."""
    caminho = Path(valor)
    return caminho if caminho.is_absolute() else RAIZ_PROJETO / caminho


def carregar_config() -> Config:
    """Lê o .env da raiz do projeto e devolve as configurações."""
    load_dotenv(RAIZ_PROJETO / ".env")
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key:
        logger.warning("LLM_API_KEY não definida: o agente não conseguirá chamar o modelo.")
    return Config(
        base_url=os.getenv("LLM_BASE_URL") or PADRAO_BASE_URL,
        api_key=api_key,
        modelo=os.getenv("LLM_MODEL") or PADRAO_MODELO,
        modelos_fallback=_separar_lista(os.getenv("LLM_FALLBACK_MODELS") or PADRAO_FALLBACK),
        db_path=_resolver_caminho(os.getenv("DB_PATH") or PADRAO_DB_PATH),
    )
