"""Testes do agente com FunctionModel (sem chamar o modelo real nem gastar cota)."""

from collections.abc import Callable, Iterator

import pydantic_ai
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cinedata_agent import __main__ as cli
from cinedata_agent import agent as modulo_agente
from cinedata_agent.agent import (
    MAX_REQUISICOES,
    ErroAgente,
    QuotaExceededError,
    RespostaAgente,
    _deve_trocar_de_modelo,
    ask,
    classificar_erro,
    criar_agente,
)
from cinedata_agent.config import RAIZ_PROJETO

CAMINHO_DB = RAIZ_PROJETO / "cinerocket.db"
precisa_db = pytest.mark.skipif(not CAMINHO_DB.exists(), reason="banco cinerocket.db ausente")

SQL_GENEROS = "SELECT nome_genero FROM dim_genres ORDER BY nome_genero"
SQL_100_TITULOS = "SELECT titulo FROM dim_movies"

FuncaoModelo = Callable[[list[ModelMessage], AgentInfo], ModelResponse]

CORPO_COTA = {"message": "Rate limit exceeded: free-models-per-day.", "code": 429}
CORPO_UPSTREAM = {
    "message": "Provider returned error",
    "code": 429,
    "metadata": {"raw": "model is temporarily rate-limited upstream", "provider_name": "Chutes"},
}
CORPO_502_PROVEDOR = {  # erro real visto na avaliação (Etapa 8)
    "message": "Provider returned error",
    "code": 502,
    "metadata": {"raw": "error code: 502\n", "provider_name": "Nvidia", "is_byok": False},
}


@pytest.fixture
def sem_banco(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita carregar o banco em testes que não executam SQL."""
    monkeypatch.setattr(modulo_agente, "inicializar_banco", lambda: None)


def roteiro(*respostas: ModelResponse, chamadas: list[list[ModelMessage]] | None = None) -> FunctionModel:
    """Modelo falso que devolve as respostas na ordem e guarda as mensagens recebidas."""
    fila: Iterator[ModelResponse] = iter(respostas)

    def funcao(mensagens: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if chamadas is not None:
            chamadas.append(mensagens)
        return next(fila)

    return FunctionModel(funcao, model_name="modelo-teste")


def chamar_sql(sql: str) -> ModelResponse:
    """Resposta do modelo pedindo para executar um SQL."""
    return ModelResponse(parts=[ToolCallPart("run_sql", {"sql": sql})])


def texto(conteudo: str) -> ModelResponse:
    """Resposta final do modelo em texto."""
    return ModelResponse(parts=[TextPart(conteudo)])


def falha_http(status: int, corpo: object, chamadas: list[str]) -> FunctionModel:
    """Modelo falso que sempre falha com erro HTTP."""

    def funcao(mensagens: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        chamadas.append("falhou")
        raise ModelHTTPError(status, "modelo-lotado", corpo)

    return FunctionModel(funcao, model_name="modelo-lotado")


def partes_retry(mensagens: list[ModelMessage]) -> list[RetryPromptPart]:
    """Erros devolvidos ao modelo (ModelRetry) presentes nas mensagens."""
    return [p for m in mensagens if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, RetryPromptPart)]


# --- Fluxo do agente ---

@precisa_db
def test_ask_devolve_resposta_sql_e_resultado_completo() -> None:
    modelo = roteiro(chamar_sql(SQL_100_TITULOS), texto("Aqui estão os títulos."))
    resposta = ask("Liste títulos", modelo=modelo)
    assert resposta.resposta == "Aqui estão os títulos."
    assert resposta.sql_executados == [f"{SQL_100_TITULOS}\nLIMIT 100"]
    assert resposta.colunas == ["titulo"]
    assert len(resposta.linhas) == 100  # resultado completo, não o recorte de 50 enviado ao modelo
    assert resposta.requisicoes == 2
    assert resposta.modelo == "modelo-teste"
    assert resposta.tempo_ms >= 0
    assert any(isinstance(m, ModelResponse) for m in resposta.historico)


@precisa_db
def test_autocorrecao_devolve_erro_em_portugues_ao_modelo() -> None:
    chamadas: list[list[ModelMessage]] = []
    modelo = roteiro(
        chamar_sql("SELECT nome FROM dim_genres"),  # coluna inexistente
        chamar_sql(SQL_GENEROS),
        texto("São 19 gêneros."),
        chamadas=chamadas,
    )
    resposta = ask("Quais gêneros existem?", modelo=modelo)
    erros = partes_retry(chamadas[1])
    assert len(erros) == 1 and "Coluna inexistente" in str(erros[0].content)
    assert resposta.sql_executados == [f"{SQL_GENEROS}\nLIMIT 100"]  # só o SQL que deu certo
    assert len(resposta.linhas) == 19
    assert resposta.requisicoes == 3


def test_desiste_depois_de_3_tentativas(sem_banco: None) -> None:
    sql_proibido = chamar_sql("DROP TABLE dim_movies")
    modelo = roteiro(*[sql_proibido] * 5)
    with pytest.raises(ErroAgente, match="consulta válida"):
        ask("Apague os filmes", modelo=modelo)


def test_limite_de_requisicoes_por_pergunta(sem_banco: None) -> None:
    chamadas: list[list[ModelMessage]] = []
    sempre_ferramenta = [ModelResponse(parts=[ToolCallPart("list_tables", {})])] * 10
    modelo = roteiro(*sempre_ferramenta, chamadas=chamadas)
    with pytest.raises(ErroAgente, match=f"limite de {MAX_REQUISICOES}"):
        ask("Pergunta que não termina", modelo=modelo)
    assert len(chamadas) == MAX_REQUISICOES


def test_memoria_da_conversa(sem_banco: None) -> None:
    primeira = ask("Qual o filme mais popular?", modelo=roteiro(texto("É o filme X.")))
    chamadas: list[list[ModelMessage]] = []
    ask("E o segundo?", historico=primeira.historico, modelo=roteiro(texto("É o Y."), chamadas=chamadas))
    perguntas = [
        p.content for m in chamadas[0] if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)
    ]
    assert perguntas == ["Qual o filme mais popular?", "E o segundo?"]


def test_banner_do_pydantic_ai_desligado() -> None:
    assert pydantic_ai.BANNER_ENABLED is False


def test_ferramentas_registradas() -> None:
    nomes = set(criar_agente()._function_toolset.tools)
    assert nomes == {"run_sql", "list_tables", "describe_table", "sample_values"}


# --- Erros do modelo e cota ---

@pytest.mark.parametrize(
    ("status", "corpo", "esperado"),
    [
        (429, CORPO_UPSTREAM, "trocar"),
        (429, "Model is temporarily rate-limited upstream", "trocar"),
        (404, {"message": "No endpoints found for modelo:free"}, "trocar"),
        (429, CORPO_COTA, "cota"),
        (502, CORPO_502_PROVEDOR, "trocar"),
        (503, {"message": "Provider returned error", "metadata": {"provider_name": "Chutes"}}, "trocar"),
        (400, {"message": "Bad request"}, "outro"),
        (500, None, "outro"),
        (502, {"message": "Bad gateway"}, "outro"),
    ],
    ids=["429_upstream", "429_upstream_texto", "404", "429_cota", "502_provedor", "503_provedor", "400", "500",
         "502_sem_provedor"],
)
def test_classificar_erro(status: int, corpo: object, esperado: str) -> None:
    assert classificar_erro(ModelHTTPError(status, "m", corpo)) == esperado


def test_classificar_erro_que_nao_e_http() -> None:
    assert classificar_erro(ValueError("x")) == "outro"
    assert _deve_trocar_de_modelo(ValueError("x")) is False


def test_cota_esgotada_nao_tenta_outros_modelos(sem_banco: None) -> None:
    falhas: list[str] = []
    reserva: list[list[ModelMessage]] = []
    modelo = FallbackModel(
        falha_http(429, CORPO_COTA, falhas),
        roteiro(texto("não deveria responder"), chamadas=reserva),
        fallback_on=_deve_trocar_de_modelo,
    )
    with pytest.raises(QuotaExceededError, match="21h"):
        ask("Quantos filmes existem?", modelo=modelo)
    assert falhas == ["falhou"]  # uma única tentativa, sem repetir
    assert reserva == []  # o modelo reserva nunca foi chamado


@pytest.mark.parametrize(
    ("status", "corpo"), [(429, CORPO_UPSTREAM), (404, {"message": "not found"}), (502, CORPO_502_PROVEDOR)]
)
def test_modelo_indisponivel_passa_para_o_proximo(sem_banco: None, status: int, corpo: object) -> None:
    falhas: list[str] = []
    modelo = FallbackModel(
        falha_http(status, corpo, falhas),
        roteiro(texto("Resposta do reserva.")),
        fallback_on=_deve_trocar_de_modelo,
    )
    resposta = ask("Quantos filmes existem?", modelo=modelo)
    assert resposta.resposta == "Resposta do reserva."
    assert resposta.modelo == "modelo-teste"
    assert falhas == ["falhou"]
    assert resposta.requisicoes == 2  # a falha também gasta cota


def test_todos_os_modelos_indisponiveis(sem_banco: None) -> None:
    falhas: list[str] = []
    modelo = FallbackModel(
        falha_http(429, CORPO_UPSTREAM, falhas),
        falha_http(404, {"message": "not found"}, falhas),
        fallback_on=_deve_trocar_de_modelo,
    )
    with pytest.raises(ErroAgente, match="indisponíveis"):
        ask("Quantos filmes existem?", modelo=modelo)
    assert falhas == ["falhou", "falhou"]


# --- CLI ---

def resposta_falsa(
    pergunta: str, historico: list[ModelMessage] | None = None, usar_cache: bool = True
) -> RespostaAgente:
    """Substitui o ask() na CLI, guardando o histórico recebido."""
    return RespostaAgente(
        resposta=f"Resposta para: {pergunta}",
        sql_executados=["SELECT 1 AS um"],
        colunas=["um"],
        linhas=[{"um": 1}],
        modelo="modelo-teste",
        requisicoes=2,
        tempo_ms=1234,
        historico=[*(historico or []), ModelRequest(parts=[UserPromptPart(pergunta)])],
    )


def test_cli_uma_pergunta(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "ask", resposta_falsa)
    assert cli.main(["Top", "10", "filmes"]) == 0
    saida = capsys.readouterr().out
    assert "Resposta para: Top 10 filmes" in saida
    assert "SELECT 1 AS um" in saida
    assert "Modelo: modelo-teste | Requisições: 2 | Tempo: 1,2 s" in saida


def test_cli_interativo_com_memoria(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    historicos: list[int] = []

    def ask_registrando(
        pergunta: str, historico: list[ModelMessage] | None = None, usar_cache: bool = True
    ) -> RespostaAgente:
        historicos.append(len(historico or []))
        return resposta_falsa(pergunta, historico)

    entradas = iter(["primeira", "segunda", "sair"])
    monkeypatch.setattr(cli, "ask", ask_registrando)
    monkeypatch.setattr(cli, "inicializar_banco", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _: next(entradas))
    assert cli.main([]) == 0
    assert historicos == [0, 1]  # a segunda pergunta recebe o histórico da primeira
    assert "Até mais!" in capsys.readouterr().out


def test_cli_cota_esgotada(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def sem_cota(
        pergunta: str, historico: list[ModelMessage] | None = None, usar_cache: bool = True
    ) -> RespostaAgente:
        raise QuotaExceededError()

    monkeypatch.setattr(cli, "ask", sem_cota)
    assert cli.main(["qualquer"]) == 2
    assert "21h" in capsys.readouterr().out


@pytest.mark.parametrize(("argv", "esperado"), [(["pergunta"], True), (["--no-cache", "pergunta"], False)])
def test_cli_opcao_no_cache(monkeypatch: pytest.MonkeyPatch, argv: list[str], esperado: bool) -> None:
    recebidos: list[bool] = []

    def ask_registrando(
        pergunta: str, historico: list[ModelMessage] | None = None, usar_cache: bool = True
    ) -> RespostaAgente:
        recebidos.append(usar_cache)
        return resposta_falsa(pergunta, historico)

    monkeypatch.setattr(cli, "ask", ask_registrando)
    assert cli.main(argv) == 0
    assert recebidos == [esperado]


def test_cli_rodape_de_cache_e_de_recusa() -> None:
    do_cache = resposta_falsa("x").model_copy(update={"cache": True, "requisicoes": 0})
    assert "Requisições: 0 (resposta do cache)" in cli.formatar_resposta(do_cache)
    recusada = RespostaAgente(
        resposta="Não posso alterar dados.", sql_executados=[], colunas=[], linhas=[],
        modelo=None, requisicoes=0, tempo_ms=0, historico=[], recusada=True,
    )
    texto_recusa = cli.formatar_resposta(recusada)
    assert "recusada antes de chamar o modelo" in texto_recusa
    assert "SQL executado" not in texto_recusa
