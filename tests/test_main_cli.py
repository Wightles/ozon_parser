"""Tests for the unified project command-line entry point."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import main as cli


def test_default_command_runs_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run_module_main(module_name: str, argv: list[str]) -> int:
        calls.append((module_name, tuple(argv)))
        return 0

    monkeypatch.setattr(cli, "_run_module_main", fake_run_module_main)

    assert cli.main([]) == 0
    assert calls == [("parse_ozon", ())]


def test_parse_command_runs_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run_module_main(module_name: str, argv: list[str]) -> int:
        calls.append((module_name, tuple(argv)))
        return 0

    monkeypatch.setattr(cli, "_run_module_main", fake_run_module_main)

    assert cli.main(["parse"]) == 0
    assert calls == [("parse_ozon", ())]


def test_auth_command_forwards_browser_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run_module_main(module_name: str, argv: list[str]) -> int:
        calls.append((module_name, tuple(argv)))
        return 0

    monkeypatch.setattr(cli, "_run_module_main", fake_run_module_main)

    result = cli.main(
        [
            "auth",
            "--cdp-url",
            "http://127.0.0.1:9223",
            "--capture-only",
        ]
    )

    assert result == 0
    assert calls == [
        (
            "get_cookies",
            ("--cdp-url", "http://127.0.0.1:9223", "--capture-only"),
        )
    ]


def test_gmail_command_forwards_diagnostic_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run_module_main(module_name: str, argv: list[str]) -> int:
        calls.append((module_name, tuple(argv)))
        return 0

    monkeypatch.setattr(cli, "_run_module_main", fake_run_module_main)

    result = cli.main(
        ["gmail", "--auth-only", "--lookback-seconds", "30", "--timeout", "5"]
    )

    assert result == 0
    assert calls == [
        (
            "gmail_client",
            ("--auth-only", "--lookback-seconds", "30.0", "--timeout", "5.0"),
        )
    ]


def test_module_runner_restores_sys_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_argv: list[str] = []

    def fake_import_module(module_name: str) -> SimpleNamespace:
        assert module_name == "parse_ozon"

        def fake_main() -> int:
            seen_argv.extend(sys.argv)
            return 7

        return SimpleNamespace(main=fake_main)

    monkeypatch.setattr(cli.importlib, "import_module", fake_import_module)
    original_argv = sys.argv[:]

    assert cli._run_module_main("parse_ozon", ["--unused"]) == 7
    assert seen_argv == ["parse_ozon.py", "--unused"]
    assert sys.argv == original_argv
