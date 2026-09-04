"""Tests for local environment diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from healthcheck import (
    REQUIRED_SCHEMA_OBJECTS,
    SCHEMA_OBJECTS_SQL,
    check_cookies,
    check_database,
    check_file,
    run_checks,
)


class FakeCursor:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, tuple[list[str]]]] = []

    def execute(self, query: str, params: tuple[list[str]]) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[tuple[str]]:
        return self.rows

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeConnection:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self.cursor_instance = FakeCursor(rows)
        self.commits = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


class FakeStorage:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.connection_checked = False

    def check_connection(self) -> None:
        self.connection_checked = True

    def __enter__(self) -> FakeStorage:
        return self

    def __exit__(self, *args: object) -> None:
        self.connection.close()


def _settings(**overrides: Any) -> SimpleNamespace:
    defaults = {
        "gmail_credentials_path": Path("credentials.json"),
        "gmail_token_path": Path("token.json"),
        "cookies_path": Path("cookies.json"),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_check_file_reports_present_and_missing(tmp_path: Path) -> None:
    present = tmp_path / "present.txt"
    missing = tmp_path / "missing.txt"
    present.write_text("ok", encoding="utf-8")

    assert check_file("present", present).ok is True
    missing_result = check_file("missing", missing)

    assert missing_result.ok is False
    assert "missing" in missing_result.message


def test_check_cookies_counts_active_ozon_cookies(tmp_path: Path) -> None:
    cookies_path = tmp_path / "cookies.json"
    cookies_path.write_text(
        json.dumps(
            [
                {
                    "name": "session_id",
                    "value": "value",
                    "domain": ".ozon.ru",
                    "path": "/",
                    "expires": 2_000_000_000,
                    "secure": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    result = check_cookies(_settings(cookies_path=cookies_path))

    assert result.ok is True
    assert result.message == "1 active cookie(s)"


def test_check_cookies_reports_invalid_cookie_file(tmp_path: Path) -> None:
    cookies_path = tmp_path / "cookies.json"
    cookies_path.write_text("[]", encoding="utf-8")

    result = check_cookies(_settings(cookies_path=cookies_path))

    assert result.ok is False
    assert result.name == "Ozon cookies"


def test_check_database_verifies_required_schema_objects(
    monkeypatch: Any,
) -> None:
    connection = FakeConnection([(name,) for name in REQUIRED_SCHEMA_OBJECTS])
    storage = FakeStorage(connection)

    monkeypatch.setattr(
        "healthcheck.PostgresProductStorage.from_settings",
        lambda _settings: storage,
    )

    result = check_database(_settings())

    assert result.ok is True
    assert storage.connection_checked is True
    assert connection.cursor_instance.executed == [
        (SCHEMA_OBJECTS_SQL, (list(REQUIRED_SCHEMA_OBJECTS),))
    ]
    assert connection.commits == 1
    assert connection.closed is True


def test_check_database_reports_missing_schema_objects(
    monkeypatch: Any,
) -> None:
    connection = FakeConnection([("products",), ("product_history",)])
    monkeypatch.setattr(
        "healthcheck.PostgresProductStorage.from_settings",
        lambda _settings: FakeStorage(connection),
    )

    result = check_database(_settings())

    assert result.ok is False
    assert "datalens_products" in result.message
    assert "datalens_product_history" in result.message


def test_run_checks_can_skip_database(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    credentials = tmp_path / "credentials.json"
    token = tmp_path / "token.json"
    cookies = tmp_path / "cookies.json"
    credentials.write_text("{}", encoding="utf-8")
    token.write_text("{}", encoding="utf-8")
    cookies.write_text(
        json.dumps(
            [
                {
                    "name": "session_id",
                    "value": "value",
                    "domain": ".ozon.ru",
                    "path": "/",
                    "expires": 2_000_000_000,
                }
            ]
        ),
        encoding="utf-8",
    )

    def fail_if_called(_settings: object) -> object:
        raise AssertionError("database should be skipped")

    monkeypatch.setattr(
        "healthcheck.PostgresProductStorage.from_settings",
        fail_if_called,
    )

    results = run_checks(
        _settings(
            gmail_credentials_path=credentials,
            gmail_token_path=token,
            cookies_path=cookies,
        ),
        include_database=False,
    )

    assert [result.name for result in results] == [
        "Gmail credentials",
        "Gmail token",
        "Ozon cookies",
    ]
    assert all(result.ok for result in results)
