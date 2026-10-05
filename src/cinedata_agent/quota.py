"""Consulta da cota diária de modelos gratuitos do OpenRouter (consultar não gasta cota)."""

import logging
from typing import Any

import httpx

from cinedata_agent.config import Config, carregar_config

logger = logging.getLogger(__name__)


class ErroCota(Exception):
    """Não foi possível consultar a cota (sem chave ou OpenRouter fora do ar)."""


def consultar_cota(config: Config | None = None) -> dict[str, Any]:
    """Devolve a cota diária de modelos :free, ex.: {'used': 9, 'limit': 50, 'remaining': 41}."""
    config = config or carregar_config()
    if not config.api_key:
        raise ErroCota("LLM_API_KEY não está configurada no .env.")
    try:
        resposta = httpx.get(
            f"{config.base_url.rstrip('/')}/key",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=10,
        )
        resposta.raise_for_status()
    except httpx.HTTPError as erro:
        logger.warning("Falha ao consultar a cota: %s", erro)
        raise ErroCota("Não foi possível consultar a cota no OpenRouter agora.") from erro
    dados = resposta.json().get("data", {})
    return dados.get("free_model_daily_requests", dados)
