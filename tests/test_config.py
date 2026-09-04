"""Tests for environment-driven application configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import Settings
from utils.exceptions import ConfigurationError


def test_default_skus_match_verified_demo_products(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OZON_SKUS", raising=False)

    settings = Settings.from_env(tmp_path / "missing.env")

    assert settings.ozon_skus == ("2359066702", "2829800382")


def test_skus_can_be_configured_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OZON_SKUS", " 2359066702,2829800382\n2359066702, 123456789 "
    )

    settings = Settings.from_env(tmp_path / "missing.env")

    assert settings.ozon_skus == (
        "2359066702",
        "2829800382",
        "123456789",
    )


@pytest.mark.parametrize("raw_value", ["", "   ", "2359066702,sku"])
def test_invalid_sku_configuration_fails_clearly(
    raw_value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OZON_SKUS", raw_value)

    with pytest.raises(ConfigurationError, match="OZON_SKUS"):
        Settings.from_env(tmp_path / "missing.env")
