"""Configuração comum dos testes."""

from pathlib import Path

import pytest

from cinedata_agent import cache


@pytest.fixture(autouse=True)
def cache_temporario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cada teste usa um cache vazio em pasta temporária, nunca o .cache/ do projeto."""
    caminho = tmp_path / ".cache" / "cache.db"
    monkeypatch.setattr(cache, "CAMINHO_CACHE", caminho)
    return caminho
