"""Tests for loading Playwright cookies without HTTP requests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ozon_client import load_cookies
from utils.exceptions import CookiesInvalidError, CookiesNotFoundError


def test_loads_valid_ozon_cookies(valid_cookies_path: Path) -> None:
    cookies = load_cookies(valid_cookies_path)

    assert len(cookies) == 1
    assert cookies[0]["name"] == "session_id"
    assert cookies[0]["domain"] == ".ozon.ru"


def test_missing_cookie_file_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(CookiesNotFoundError, match="Cookies file is missing"):
        load_cookies(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps([{"name": "session", "value": "value"}]),
        json.dumps(
            [
                {
                    "name": "session",
                    "value": "value",
                    "domain": "ozon.ru.example.com",
                }
            ]
        ),
    ],
)
def test_rejects_invalid_cookie_files(tmp_path: Path, content: str) -> None:
    path = tmp_path / "cookies.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(CookiesInvalidError):
        load_cookies(path)
