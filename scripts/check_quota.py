"""Mostra a cota diária de modelos gratuitos do OpenRouter (consultar não gasta cota)."""

from cinedata_agent.quota import consultar_cota

if __name__ == "__main__":
    print(consultar_cota())
