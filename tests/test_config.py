"""Testes da leitura de configurações (sem chamar o modelo)."""

from pathlib import Path

import pytest

from cinedata_agent import config

VARIAVEIS = ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_FALLBACK_MODELS", "DB_PATH", "DB_TIMEOUT_S"]


@pytest.fixture
def ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove as variáveis do ambiente e impede a leitura do .env real."""
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)
    return monkeypatch


def test_usa_padroes_sem_env(ambiente_limpo: pytest.MonkeyPatch) -> None:
    cfg = config.carregar_config()
    assert cfg.base_url == config.PADRAO_BASE_URL
    assert cfg.modelo == config.PADRAO_MODELO
    assert cfg.api_key == ""
    assert cfg.db_path == config.RAIZ_PROJETO / "cinerocket.db"
    assert cfg.db_timeout_s == 15.0
    assert all(modelo for modelo in cfg.modelos_fallback)


def test_le_variaveis_e_separa_fallback(ambiente_limpo: pytest.MonkeyPatch) -> None:
    ambiente_limpo.setenv("LLM_MODEL", "modelo-a:free")
    ambiente_limpo.setenv("LLM_FALLBACK_MODELS", " modelo-b:free , ,modelo-c:free,")
    ambiente_limpo.setenv("LLM_API_KEY", "segredo")
    cfg = config.carregar_config()
    assert cfg.modelo == "modelo-a:free"
    assert cfg.modelos_fallback == ("modelo-b:free", "modelo-c:free")
    assert "segredo" not in repr(cfg)


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("10", 10.0), ("7,5", 7.5), (" 20 ", 20.0), ("", 15.0), ("abc", 15.0), ("0", 15.0), ("-3", 15.0)],
)
def test_db_timeout_s(ambiente_limpo: pytest.MonkeyPatch, valor: str, esperado: float) -> None:
    ambiente_limpo.setenv("DB_TIMEOUT_S", valor)
    assert config.carregar_config().db_timeout_s == esperado


def test_db_path_absoluto_e_mantido(ambiente_limpo: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ambiente_limpo.setenv("DB_PATH", str(tmp_path / "outro.db"))
    assert config.carregar_config().db_path == tmp_path / "outro.db"
