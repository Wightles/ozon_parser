"""Focused tests for resilient Ozon login-page inspection."""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError

from get_cookies import (
    OzonBrowserAuthenticator,
    is_ozon_hostname,
    phone_for_ozon_id,
)


class NavigatingLocator:
    """Simulate a locator whose JavaScript context vanished on redirect."""

    def evaluate_all(self, _expression: str) -> list[object]:
        raise PlaywrightError("Execution context was destroyed")


class StaticLocator:
    def __init__(self, frame_name: str, controls: list[dict[str, object]]) -> None:
        self.frame_name = frame_name
        self.controls = controls

    def evaluate_all(self, _expression: str) -> list[dict[str, object]]:
        return self.controls

    def nth(self, index: int) -> tuple[str, int]:
        return self.frame_name, index


class StaticFrame:
    def __init__(self, name: str, controls: list[dict[str, object]]) -> None:
        self.name = name
        self.controls = controls

    def locator(self, _selector: str) -> StaticLocator:
        return StaticLocator(self.name, self.controls)


class FramedPage:
    def __init__(self) -> None:
        self.frames = [
            StaticFrame(
                "main",
                [
                    {
                        "index": 0,
                        "type": "text",
                        "name": "search",
                        "placeholder": "Искать на Ozon",
                    }
                ],
            ),
            StaticFrame(
                "ozon-id",
                [{"index": 0, "type": "tel", "name": "autocomplete"}],
            ),
        ]


def test_control_metadata_retries_after_page_navigation() -> None:
    metadata = OzonBrowserAuthenticator._control_metadata(NavigatingLocator())

    assert metadata == []


def test_recognizes_only_ozon_hostnames() -> None:
    assert is_ozon_hostname("ozon.ru")
    assert is_ozon_hostname("www.ozon.ru")
    assert is_ozon_hostname("data.ozon.ru")
    assert not is_ozon_hostname("ozon.ru.example.com")
    assert not is_ozon_hostname(None)


def test_finds_phone_input_inside_ozon_id_frame() -> None:
    authenticator = object.__new__(OzonBrowserAuthenticator)

    control = authenticator._find_input(FramedPage(), "phone")

    assert control == ("ozon-id", 0)


def test_strips_russian_country_prefix_for_ozon_id() -> None:
    assert phone_for_ozon_id("+7 (999) 123-45-67") == "9991234567"
    assert phone_for_ozon_id("8 999 123 45 67") == "9991234567"


def test_prefers_exact_login_button_inside_ozon_id_frame() -> None:
    authenticator = object.__new__(OzonBrowserAuthenticator)
    page = FramedPage()
    page.frames = [
        StaticFrame(
            "ozon-id",
            [
                {"index": 0, "type": "button", "text": "Войти с VK ID"},
                {"index": 1, "type": "button", "text": "Войти"},
                {"index": 2, "type": "button", "text": "Войти по почте"},
            ],
        )
    ]

    control = authenticator._find_action(page, "request_code")

    assert control == ("ozon-id", 1)
