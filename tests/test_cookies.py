"""Tests for loading Playwright cookies without HTTP requests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ozon_client import (
    OzonBrowserClient,
    load_cookies,
    validate_product_document,
)
from utils.exceptions import (
    CookiesInvalidError,
    CookiesNotFoundError,
    OzonAntiBotError,
    ProductPageError,
)


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


def test_browser_transport_accepts_only_local_cdp_endpoint() -> None:
    with pytest.raises(ProductPageError, match="local computer"):
        OzonBrowserClient(
            "https://remote.example:9223",
            navigation_timeout=30,
        )

    client = OzonBrowserClient(
        "http://127.0.0.1:9223",
        navigation_timeout=30,
    )
    assert client.cdp_url == "http://127.0.0.1:9223"


def test_classifies_ozon_no_connection_page_as_anti_bot() -> None:
    with pytest.raises(OzonAntiBotError, match="anti-bot"):
        validate_product_document(
            status_code=200,
            url="https://www.ozon.ru/product/2359066702/",
            content_type="text/html; charset=utf-8",
            html="<html><body>Похоже, нет соединения</body></html>",
            sku="2359066702",
        )
