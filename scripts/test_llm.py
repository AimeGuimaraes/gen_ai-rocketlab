import os
import sys

from dotenv import load_dotenv
from openai import APIStatusError, OpenAI

load_dotenv()

modelo = sys.argv[1] if len(sys.argv) > 1 else os.getenv("LLM_MODEL")
client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"))

tools = [
    {
        "type": "function",
        "function": {
            "name": "contar_filmes",
            "description": "Retorna a quantidade total de filmes no catálogo.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
]

print(f"Testando: {modelo}")
try:
    resposta = client.chat.completions.create(
        model=modelo,
        messages=[{"role": "user", "content": "Quantos filmes existem no catálogo?"}],
        tools=tools,
    )
except APIStatusError as erro:
    print(f"Erro {erro.status_code}: {erro.message}")
    sys.exit(1)

msg = resposta.choices[0].message
if msg.tool_calls:
    print(f"OK, chamou a ferramenta: {msg.tool_calls[0].function.name}")
else:
    print(f"NÃO chamou ferramenta. Respondeu só texto: {msg.content}")
