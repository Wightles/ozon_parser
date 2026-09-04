"""Tests for actionable recovery hints on expected failures."""

from __future__ import annotations

from utils.exceptions import (
    CookiesExpiredError,
    GmailAuthenticationError,
    OzonAntiBotError,
    StorageError,
    recovery_hint,
)


def test_cookie_hint_points_to_auth_refresh() -> None:
    hint = recovery_hint(CookiesExpiredError("expired"))

    assert hint is not None
    assert "python3 main.py auth" in hint


def test_gmail_hint_points_to_oauth_refresh() -> None:
    hint = recovery_hint(GmailAuthenticationError("missing token"))

    assert hint is not None
    assert "python3 main.py gmail --auth-only" in hint


def test_storage_hint_points_to_doctor_and_csv_only() -> None:
    hint = recovery_hint(StorageError("database down"))

    assert hint is not None
    assert "python3 main.py doctor" in hint
    assert "python3 main.py parse --csv-only" in hint


def test_anti_bot_hint_keeps_manual_boundary() -> None:
    hint = recovery_hint(OzonAntiBotError("blocked"))

    assert hint is not None
    assert "manually" in hint
