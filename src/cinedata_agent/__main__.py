"""CLI do agente: python -m cinedata_agent "pergunta" ou, sem argumentos, modo interativo."""

import argparse
import logging
import sys

import pandas as pd
from pydantic_ai.messages import ModelMessage

from cinedata_agent.agent import ErroAgente, QuotaExceededError, RespostaAgente, ask
from cinedata_agent.db import inicializar_banco

MAX_LINHAS_TABELA = 20
COMANDOS_SAIR = {"sair", "exit", "quit"}

# Na CLI, print é a saída para o usuário; os passos internos vão para o logging.


def formatar_resposta(resposta: RespostaAgente) -> str:
    """Monta o texto mostrado no terminal: resposta, SQL, tabela e rodapé."""
    partes = [resposta.resposta.strip()]
    if resposta.sql_executados:
        sqls = "\n\n".join(resposta.sql_executados)
        partes.append(f"--- SQL executado ---\n{sqls}")
    if resposta.linhas:
        tabela = pd.DataFrame(resposta.linhas[:MAX_LINHAS_TABELA], columns=resposta.colunas)
        texto = tabela.to_string(index=False)
        if len(resposta.linhas) > MAX_LINHAS_TABELA:
            texto += f"\n(mostrando {MAX_LINHAS_TABELA} de {len(resposta.linhas)} linhas)"
        partes.append(f"--- Resultado da última consulta ---\n{texto}")
    tempo = f"{resposta.tempo_ms / 1000:.1f}".replace(".", ",")
    partes.append(
        f"Modelo: {resposta.modelo or '?'} | Requisições: {resposta.requisicoes} | Tempo: {tempo} s"
    )
    return "\n\n".join(partes)


def responder(pergunta: str, historico: list[ModelMessage] | None = None) -> RespostaAgente | None:
    """Faz a pergunta e imprime o resultado; devolve None em caso de erro do agente."""
    try:
        resposta = ask(pergunta, historico)
    except ErroAgente as erro:
        print(f"Não consegui responder: {erro}")
        return None
    print(formatar_resposta(resposta))
    return resposta


def modo_interativo() -> None:
    """Responde várias perguntas seguidas, com memória da conversa, até o usuário digitar "sair"."""
    print("Carregando o banco...")
    inicializar_banco()
    print('CineData Agent. Faça sua pergunta (ou digite "sair").')
    historico: list[ModelMessage] | None = None
    while True:
        try:
            pergunta = input("\nPergunta> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not pergunta:
            continue
        if pergunta.lower() in COMANDOS_SAIR:
            break
        resposta = responder(pergunta, historico)
        if resposta is not None:
            historico = resposta.historico
    print("Até mais!")


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da CLI; devolve o código de saída."""
    parser = argparse.ArgumentParser(
        prog="python -m cinedata_agent",
        description="Pergunte em português sobre o catálogo de filmes da CineData.",
    )
    parser.add_argument("pergunta", nargs="*", help="pergunta (sem ela, abre o modo interativo)")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra os passos do agente (logging)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.pergunta:
            return 0 if responder(" ".join(args.pergunta)) is not None else 1
        modo_interativo()
    except QuotaExceededError as erro:
        print(erro)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
