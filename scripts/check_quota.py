import os

import httpx
from dotenv import load_dotenv

load_dotenv()

resposta = httpx.get(
    "https://openrouter.ai/api/v1/key",
    headers={"Authorization": f"Bearer {os.getenv('LLM_API_KEY')}"},
    timeout=10,
)
resposta.raise_for_status()
dados = resposta.json().get("data", {})
print(dados.get("free_model_daily_requests", dados))