"""Mostra a cota diária de modelos gratuitos do OpenRouter (consultar não gasta cota)."""

import os
from typing import Any

import httpx
from dotenv import load_dotenv


def consultar_cota() -> dict[str, Any]:
    """Devolve a cota diária de modelos :free, ex.: {'used': 9, 'limit': 50, 'remaining': 41}."""
    load_dotenv()
    resposta = httpx.get(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {os.getenv('LLM_API_KEY')}"},
        timeout=10,
    )
    resposta.raise_for_status()
    dados = resposta.json().get("data", {})
    return dados.get("free_model_daily_requests", dados)


if __name__ == "__main__":
    print(consultar_cota())
