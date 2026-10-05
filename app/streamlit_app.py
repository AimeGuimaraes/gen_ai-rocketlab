"""Interface Streamlit do CineData Agent: chat com tabela, SQL e gráfico automático.

Para subir: streamlit run app/streamlit_app.py (abre em http://localhost:8501).
Atalho: http://localhost:8501/?pergunta=... já faz a pergunta ao abrir.
"""

import logging
import threading
from typing import Any

import pandas as pd
import streamlit as st

from cinedata_agent import agent, quota
from cinedata_agent.config import carregar_config
from cinedata_agent.db import inicializar_banco
from cinedata_agent.graficos import criar_figura, escolher_grafico
from cinedata_agent.guardrails import MAX_CARACTERES

logger = logging.getLogger(__name__)

# Uma pergunta por categoria do enunciado (todas já respondidas no cache: não gastam cota).
EXEMPLOS: dict[str, str] = {
    "Bilheteria e finanças": "Quais são os 10 filmes com maior receita em R$?",
    "Popularidade e engajamento": "Qual é a nota média IMDb por ano de lançamento?",
    "Elenco e equipe": "Quais diretores têm a maior nota média, com no mínimo 5 filmes?",
    "Gêneros e produtoras": "Qual é a quantidade de filmes por gênero?",
    "Avaliações dos usuários": "Quais são os filmes mais avaliados pelos usuários?",
}

MENSAGEM_COTA_APP = (
    f"⏳ {agent.MENSAGEM_COTA} Enquanto isso, as perguntas de exemplo da barra lateral "
    "continuam funcionando, porque já estão guardadas no cache."
)
MENSAGEM_ERRO = "Algo deu errado ao responder. Tente de novo em instantes."


# --- Recursos compartilhados ---

@st.cache_resource
def _preparar_banco() -> None:
    """Carrega o banco em memória uma vez por processo."""
    inicializar_banco()


@st.cache_resource
def _trava() -> threading.Lock:
    """Uma pergunta por vez: o ask() usa um único loop assíncrono."""
    return threading.Lock()


@st.cache_data(ttl=60, show_spinner=False)
def _consultar_cota() -> dict[str, Any] | None:
    """Cota do OpenRouter (consultar não gasta cota); None se não der para consultar."""
    try:
        return quota.consultar_cota()
    except quota.ErroCota as erro:
        logger.warning("Cota indisponível na interface: %s", erro)
        return None


# --- Estado da conversa ---

def _iniciar_estado() -> None:
    """Cria as chaves do st.session_state na primeira execução."""
    st.session_state.setdefault("mensagens", [])  # o que aparece na tela
    st.session_state.setdefault("historico", None)  # mensagens do PydanticAI (memória da conversa)
    st.session_state.setdefault("ultimo_modelo", None)
    st.session_state.setdefault("pendente", None)  # pergunta vinda de um botão ou da URL


def _nova_conversa() -> None:
    """Apaga a conversa (tela e memória do agente)."""
    st.session_state.mensagens = []
    st.session_state.historico = None


def _perguntar(pergunta: str) -> dict[str, Any]:
    """Chama o agente e devolve a mensagem do assistente para exibir (resposta ou aviso de erro)."""
    try:
        with _trava():
            resposta = agent.ask(pergunta, st.session_state.historico)
    except agent.QuotaExceededError:
        return {"papel": "assistant", "aviso": MENSAGEM_COTA_APP}
    except agent.ErroAgente as erro:
        return {"papel": "assistant", "erro": str(erro)}
    except Exception:
        logger.exception("Erro inesperado na interface.")
        return {"papel": "assistant", "erro": MENSAGEM_ERRO}
    finally:
        _consultar_cota.clear()  # a cota pode ter mudado

    st.session_state.historico = resposta.historico
    if resposta.modelo:
        st.session_state.ultimo_modelo = resposta.modelo
    return {
        "papel": "assistant",
        "texto": resposta.resposta,
        "colunas": resposta.colunas,
        "linhas": resposta.linhas,
        "sql": resposta.sql_executados,
        "detalhes": _detalhes(resposta),
    }


def _detalhes(resposta: agent.RespostaAgente) -> str:
    """Linha discreta com a origem da resposta, as requisições e o tempo."""
    if resposta.recusada:
        return "Recusada antes de chamar o modelo (0 requisições)."
    if resposta.cache:
        return f"Resposta do cache (0 requisições) · {resposta.tempo_ms} ms"
    return f"{resposta.modelo} · {resposta.requisicoes} requisições · {resposta.tempo_ms / 1000:.1f} s"


# --- Exibição ---

def _mostrar(mensagem: dict[str, Any], indice: int) -> None:
    """Desenha uma mensagem do chat: texto, tabela, gráfico e SQL."""
    with st.chat_message(mensagem["papel"]):
        if "aviso" in mensagem:
            st.warning(mensagem["aviso"])
            return
        if "erro" in mensagem:
            st.error(mensagem["erro"])
            return
        st.markdown(mensagem["texto"].replace("$", r"\$"))  # "R$" não pode virar fórmula LaTeX
        linhas = mensagem.get("linhas") or []
        if linhas:
            colunas = mensagem.get("colunas") or list(linhas[0])
            st.dataframe(pd.DataFrame(linhas, columns=colunas), hide_index=True, width="stretch")
            grafico = escolher_grafico(colunas, linhas)
            if grafico is not None:
                st.plotly_chart(criar_figura(grafico, linhas), width="stretch", key=f"grafico-{indice}")
        if mensagem.get("sql"):
            with st.expander("SQL executado"):
                for sql in mensagem["sql"]:
                    st.code(sql, language="sql")
        if mensagem.get("detalhes"):
            st.caption(mensagem["detalhes"])


def _barra_lateral() -> None:
    """Cota, modelos, nova conversa e perguntas de exemplo."""
    with st.sidebar:
        st.header("🎬 CineData Agent")

        st.subheader("Cota do OpenRouter")
        cota = _consultar_cota()
        if cota is None:
            st.info("Não foi possível consultar a cota agora.")
        else:
            restante = cota.get("remaining")
            st.metric("Requisições restantes hoje", f"{restante} de {cota.get('limit')}")
            if restante == 0:
                st.warning("A cota de hoje acabou. As perguntas de exemplo ainda funcionam (cache).")

        st.subheader("Modelo")
        config = carregar_config()
        st.markdown(f"**Principal:** `{config.modelo}`")
        if config.modelos_fallback:
            st.markdown("**Reservas:** " + ", ".join(f"`{m}`" for m in config.modelos_fallback))
        if st.session_state.ultimo_modelo:
            st.markdown(f"**Última resposta:** `{st.session_state.ultimo_modelo}`")

        st.button("🔄 Nova conversa", on_click=_nova_conversa, width="stretch")

        st.subheader("Perguntas de exemplo")
        st.caption("Já estão no cache: respondem na hora, sem gastar cota.")
        for categoria, pergunta in EXEMPLOS.items():
            if st.button(pergunta, help=categoria, key=f"exemplo-{categoria}", width="stretch"):
                st.session_state.pendente = pergunta


def main() -> None:
    """Monta a página e trata a pergunta da vez."""
    st.set_page_config(page_title="CineData Agent", page_icon="🎬", layout="wide")
    _iniciar_estado()
    _preparar_banco()

    pergunta_url = st.query_params.get("pergunta")
    if pergunta_url and not st.session_state.get("url_respondida"):
        st.session_state.url_respondida = True
        st.session_state.pendente = pergunta_url

    _barra_lateral()

    st.title("CineData Agent")
    st.caption("Pergunte em português sobre o catálogo de filmes. Cada pergunta nova gasta cota do OpenRouter.")

    for indice, mensagem in enumerate(st.session_state.mensagens):
        _mostrar(mensagem, indice)

    digitada = st.chat_input("Ex.: Quais são os 5 filmes mais populares?", max_chars=MAX_CARACTERES)
    pergunta = digitada or st.session_state.pendente
    st.session_state.pendente = None
    if not pergunta:
        return

    st.session_state.mensagens.append({"papel": "user", "texto": pergunta})
    _mostrar(st.session_state.mensagens[-1], len(st.session_state.mensagens) - 1)
    with st.spinner("Consultando o banco..."):
        st.session_state.mensagens.append(_perguntar(pergunta))
    st.rerun()  # redesenha a conversa e a barra lateral (cota e último modelo)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
main()
