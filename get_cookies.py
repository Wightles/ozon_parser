"""Authorize on data.ozon.ru with Playwright and save browser cookies."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from playwright.sync_api import (
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from config import Settings, get_settings
from gmail_client import GmailClient
from logging_config import configure_logging
from utils.exceptions import (
    OzonCaptchaError,
    OzonLoginError,
    OzonParserError,
)


LOGGER = logging.getLogger(__name__)
InputKind = Literal["phone", "code"]
ActionKind = Literal["open_login", "request_code", "submit_code"]

CAPTCHA_MARKERS = (
    "captcha",
    "капча",
    "я не робот",
    "подтвердите, что вы не робот",
    "проверка безопасности",
    "checking your browser",
    "cloudflare",
)
LOGIN_ERROR_MARKERS = (
    "неверный код",
    "код не подош",
    "истек срок действия кода",
    "code is invalid",
    "incorrect code",
)

_PHONE_WORDS = ("phone", "tel", "телефон", "номер", "+7")
_CODE_WORDS = (
    "code",
    "otp",
    "verification",
    "confirm",
    "код",
    "подтверж",
)
_ACTION_WORDS: dict[ActionKind, tuple[str, ...]] = {
    "open_login": (
        "войти или зарегистрироваться",
        "войти",
        "sign in",
        "log in",
    ),
    "request_code": (
        "получить код",
        "отправить код",
        "продолжить",
        "get code",
        "send code",
        "continue",
    ),
    "submit_code": (
        "подтвердить",
        "продолжить",
        "войти",
        "confirm",
        "continue",
        "sign in",
    ),
}


@dataclass(frozen=True, slots=True)
class ControlMetadata:
    """Non-sensitive attributes used to choose a visible form control."""

    index: int
    type: str
    name: str
    placeholder: str
    aria_label: str
    autocomplete: str
    input_mode: str
    max_length: int
    text: str
    disabled: bool
    read_only: bool

    @property
    def searchable_text(self) -> str:
        return " ".join(
            (
                self.type,
                self.name,
                self.placeholder,
                self.aria_label,
                self.autocomplete,
                self.input_mode,
                self.text,
            )
        ).casefold()


def normalize_phone(raw_phone: str) -> str:
    """Remove visual separators and validate an international phone number."""
    normalized = re.sub(r"[\s()\-]", "", raw_phone.strip())
    if not re.fullmatch(r"\+?[0-9]{10,15}", normalized):
        raise OzonLoginError(
            "OZON_PHONE must contain 10-15 digits with an optional leading +"
        )
    return normalized


def save_cookies(cookies: Sequence[Mapping[str, Any]], path: Path) -> None:
    """Serialize Playwright cookies without logging their contents."""
    if not cookies:
        raise OzonLoginError("Authorization completed without browser cookies")

    serialized = [dict(cookie) for cookie in cookies]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(serialized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        path.chmod(0o600)
    except (OSError, TypeError, ValueError) as exc:
        raise OzonLoginError(f"Cannot save cookies to {path}") from exc


class OzonBrowserAuthenticator:
    """Drive the Ozon login form while keeping CAPTCHA completion manual."""

    def __init__(self, settings: Settings, gmail_client: GmailClient) -> None:
        for name, value in (
            ("OZON_NAVIGATION_TIMEOUT", settings.ozon_navigation_timeout),
            ("OZON_LOGIN_TIMEOUT", settings.ozon_login_timeout),
            ("OZON_MANUAL_TIMEOUT", settings.ozon_manual_timeout),
        ):
            if value <= 0:
                raise OzonLoginError(f"{name} must be greater than zero")

        login_url = urlparse(settings.ozon_login_url)
        if login_url.scheme != "https" or login_url.hostname != "data.ozon.ru":
            raise OzonLoginError(
                "OZON_LOGIN_URL must be an https://data.ozon.ru URL"
            )

        self.settings = settings
        self.gmail_client = gmail_client

    def authorize(self) -> None:
        """Run the browser login flow and persist the resulting cookies."""
        phone = normalize_phone(self.settings.require_ozon_phone())
        LOGGER.info("Starting Ozon authorization")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.settings.ozon_headless
            )
            context = browser.new_context(locale="ru-RU")
            context.set_default_timeout(
                self.settings.ozon_navigation_timeout * 1000
            )
            page = context.new_page()
            try:
                self._open_login_page(page)
                phone_input = self._wait_for_phone_input(page)
                LOGGER.info("Ozon phone form detected")
                phone_input.fill(phone)

                requested_at = datetime.now(UTC)
                self._request_verification_code(page, phone_input)
                LOGGER.info("Verification code requested")

                code_control = self._wait_for_code_control(page)
                code = self.gmail_client.wait_for_verification_code(
                    requested_at
                )
                self._fill_verification_code(page, code_control, code)
                self._submit_verification_code(page, code_control)
                self._wait_for_authorization(page)

                cookies = context.cookies()
                save_cookies(cookies, self.settings.cookies_path)
                LOGGER.info("Authorization completed")
                LOGGER.info("Cookies saved to %s", self.settings.cookies_path)
            finally:
                context.close()
                browser.close()

    def _open_login_page(self, page: Page) -> None:
        LOGGER.info("Opening data.ozon.ru")
        try:
            page.goto(
                self.settings.ozon_login_url,
                wait_until="domcontentloaded",
                timeout=self.settings.ozon_navigation_timeout * 1000,
            )
        except PlaywrightTimeoutError:
            if self._find_input(page, "phone") is not None:
                return
            if self._captcha_present(page):
                self._wait_for_manual_captcha(page)
                return
            raise OzonLoginError(
                "Timed out while opening data.ozon.ru"
            ) from None
        except PlaywrightError as exc:
            message = str(exc).casefold()
            if "too_many_redirects" in message or "redirect" in message:
                raise OzonCaptchaError(
                    "Ozon returned a protective redirect loop. Retry in a visible "
                    "browser from a network accepted by Ozon."
                ) from exc
            raise OzonLoginError("Cannot open data.ozon.ru") from exc

    def _wait_for_phone_input(self, page: Page) -> Locator:
        deadline = time.monotonic() + self.settings.ozon_login_timeout
        login_button_clicked = False
        while time.monotonic() < deadline:
            phone_input = self._find_input(page, "phone")
            if phone_input is not None:
                return phone_input

            if self._captcha_present(page):
                self._wait_for_manual_captcha(page)
                continue

            if not login_button_clicked:
                login_button = self._find_action(page, "open_login")
                if login_button is not None:
                    login_button.click()
                    login_button_clicked = True
                    continue

            page.wait_for_timeout(500)

        self._log_control_diagnostics(page)
        raise OzonLoginError(
            "Ozon phone input was not found in the current page DOM"
        )

    def _request_verification_code(
        self, page: Page, phone_input: Locator
    ) -> None:
        action = self._find_action(page, "request_code")
        if action is not None:
            action.click()
            return
        phone_input.press("Enter")

    def _wait_for_code_control(self, page: Page) -> list[Locator]:
        deadline = time.monotonic() + self.settings.ozon_login_timeout
        while time.monotonic() < deadline:
            code_controls = self._find_code_controls(page)
            if code_controls:
                return code_controls
            if self._captcha_present(page):
                self._wait_for_manual_captcha(page)
                continue
            page.wait_for_timeout(500)

        self._log_control_diagnostics(page)
        raise OzonLoginError(
            "Ozon verification-code input did not appear after requesting a code"
        )

    def _fill_verification_code(
        self, page: Page, controls: list[Locator], code: str
    ) -> None:
        del page
        LOGGER.info("Entering Gmail verification code in Ozon")
        if len(controls) == 1:
            controls[0].fill(code)
            return
        if len(controls) != len(code):
            raise OzonLoginError(
                "Ozon displayed split code inputs with an unexpected length"
            )
        for control, digit in zip(controls, code, strict=True):
            control.fill(digit)

    def _submit_verification_code(
        self, page: Page, controls: list[Locator]
    ) -> None:
        page.wait_for_timeout(500)
        if not self._find_code_controls(page):
            return
        action = self._find_action(page, "submit_code")
        if action is not None:
            action.click()
            return
        controls[-1].press("Enter")

    def _wait_for_authorization(self, page: Page) -> None:
        deadline = time.monotonic() + self.settings.ozon_login_timeout
        successful_observations = 0
        while time.monotonic() < deadline:
            page_text = self._page_text(page)
            lowered_text = page_text.casefold()
            if any(marker in lowered_text for marker in LOGIN_ERROR_MARKERS):
                raise OzonLoginError(
                    "Ozon rejected the verification code or it expired"
                )
            if self._captcha_present(page):
                self._wait_for_manual_captcha(page)
                successful_observations = 0
                continue

            hostname = urlparse(page.url).hostname or ""
            code_controls = self._find_code_controls(page)
            phone_input = self._find_input(page, "phone")
            looks_authorized = (
                hostname == "data.ozon.ru"
                and not code_controls
                and phone_input is None
                and bool(page_text.strip())
            )
            successful_observations = (
                successful_observations + 1 if looks_authorized else 0
            )
            if successful_observations >= 2:
                return
            page.wait_for_timeout(500)

        raise OzonLoginError(
            "Ozon authorization did not complete before the timeout"
        )

    def _wait_for_manual_captcha(self, page: Page) -> None:
        if self.settings.ozon_headless:
            raise OzonCaptchaError(
                "Ozon anti-bot protection was detected. Set OZON_HEADLESS=false "
                "and complete the challenge manually."
            )

        LOGGER.warning(
            "Ozon anti-bot protection detected. Complete it manually in the "
            "browser within %.0f seconds; it will not be bypassed automatically.",
            self.settings.ozon_manual_timeout,
        )
        deadline = time.monotonic() + self.settings.ozon_manual_timeout
        while time.monotonic() < deadline:
            if not self._captcha_present(page):
                LOGGER.info("Manual anti-bot check completed")
                return
            page.wait_for_timeout(1000)
        raise OzonCaptchaError(
            "Manual Ozon anti-bot check was not completed before the timeout"
        )

    def _find_input(self, page: Page, kind: InputKind) -> Locator | None:
        inputs, metadata = self._visible_inputs(page)
        scored = [
            (self._input_score(item, kind), item.index)
            for item in metadata
            if not item.disabled and not item.read_only
        ]
        scored = [candidate for candidate in scored if candidate[0] >= 40]
        if not scored:
            return None
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return inputs.nth(scored[0][1])

    def _find_code_controls(self, page: Page) -> list[Locator]:
        inputs, metadata = self._visible_inputs(page)
        single_input = self._find_input(page, "code")
        if single_input is not None:
            return [single_input]

        split_indexes = [
            item.index
            for item in metadata
            if not item.disabled
            and not item.read_only
            and item.max_length == 1
            and (
                item.input_mode in {"numeric", "decimal"}
                or item.type in {"number", "tel", "text"}
            )
        ]
        if 4 <= len(split_indexes) <= 8:
            return [inputs.nth(index) for index in split_indexes]
        return []

    @staticmethod
    def _input_score(item: ControlMetadata, kind: InputKind) -> int:
        text = item.searchable_text
        if kind == "phone":
            score = 0
            if item.type == "tel":
                score += 100
            if "tel" in item.autocomplete:
                score += 80
            if any(word in text for word in _PHONE_WORDS):
                score += 60
            if item.input_mode == "tel":
                score += 40
            return score

        score = 0
        if item.autocomplete == "one-time-code":
            score += 100
        if any(word in text for word in _CODE_WORDS):
            score += 70
        if 4 <= item.max_length <= 8:
            score += 40
        if item.input_mode in {"numeric", "decimal"}:
            score += 10
        return score

    def _find_action(self, page: Page, kind: ActionKind) -> Locator | None:
        controls = page.locator(
            'button:visible, [role="button"]:visible, '
            'input[type="submit"]:visible'
        )
        metadata = self._control_metadata(controls)
        scored: list[tuple[int, int]] = []
        for item in metadata:
            if item.disabled:
                continue
            normalized_text = item.searchable_text
            score = max(
                (
                    100 - position
                    for position, word in enumerate(_ACTION_WORDS[kind])
                    if word in normalized_text
                ),
                default=0,
            )
            if score:
                scored.append((score, item.index))

        if not scored:
            enabled = [item.index for item in metadata if not item.disabled]
            return controls.nth(enabled[0]) if len(enabled) == 1 else None
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return controls.nth(scored[0][1])

    def _visible_inputs(
        self, page: Page
    ) -> tuple[Locator, list[ControlMetadata]]:
        inputs = page.locator("input:visible")
        return inputs, self._control_metadata(inputs)

    @staticmethod
    def _control_metadata(controls: Locator) -> list[ControlMetadata]:
        raw_items = controls.evaluate_all(
            """
            elements => elements.map((element, index) => ({
                index,
                type: element.getAttribute('type') || '',
                name: element.getAttribute('name') || '',
                placeholder: element.getAttribute('placeholder') || '',
                ariaLabel: element.getAttribute('aria-label') || '',
                autocomplete: element.getAttribute('autocomplete') || '',
                inputMode: element.getAttribute('inputmode') || '',
                maxLength: Number.isFinite(element.maxLength) ?
                    element.maxLength : -1,
                text: element.innerText || element.value || '',
                disabled: Boolean(element.disabled) ||
                    element.getAttribute('aria-disabled') === 'true',
                readOnly: Boolean(element.readOnly),
            }))
            """
        )
        if not isinstance(raw_items, list):
            return []

        metadata: list[ControlMetadata] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            metadata.append(
                ControlMetadata(
                    index=int(raw_item.get("index", -1)),
                    type=str(raw_item.get("type", "")).casefold(),
                    name=str(raw_item.get("name", "")),
                    placeholder=str(raw_item.get("placeholder", "")),
                    aria_label=str(raw_item.get("ariaLabel", "")),
                    autocomplete=str(
                        raw_item.get("autocomplete", "")
                    ).casefold(),
                    input_mode=str(raw_item.get("inputMode", "")).casefold(),
                    max_length=int(raw_item.get("maxLength", -1)),
                    text=str(raw_item.get("text", "")),
                    disabled=bool(raw_item.get("disabled", False)),
                    read_only=bool(raw_item.get("readOnly", False)),
                )
            )
        return metadata

    def _captcha_present(self, page: Page) -> bool:
        url = page.url.casefold()
        if "captcha" in url:
            return True
        text = self._page_text(page).casefold()
        return any(marker in text for marker in CAPTCHA_MARKERS)

    @staticmethod
    def _page_text(page: Page) -> str:
        try:
            return page.locator("body").inner_text(timeout=2000)
        except (PlaywrightError, PlaywrightTimeoutError):
            return ""

    def _log_control_diagnostics(self, page: Page) -> None:
        _, inputs = self._visible_inputs(page)
        buttons = self._control_metadata(
            page.locator(
                'button:visible, [role="button"]:visible, '
                'input[type="submit"]:visible'
            )
        )
        LOGGER.error(
            "Login DOM diagnostics: url=%s visible_inputs=%d visible_buttons=%d",
            page.url,
            len(inputs),
            len(buttons),
        )


def main() -> int:
    try:
        settings = get_settings()
    except OzonParserError as exc:
        configure_logging()
        LOGGER.error("Cannot load application settings: %s", exc)
        return 1

    configure_logging(settings.log_level)
    try:
        gmail_client = GmailClient.from_settings(settings)
        authenticator = OzonBrowserAuthenticator(settings, gmail_client)
        authenticator.authorize()
    except OzonParserError as exc:
        LOGGER.error("Ozon authorization failed: %s", exc)
        return 1
    except PlaywrightError as exc:
        LOGGER.error(
            "Playwright failed to start or control Chromium. Run "
            "`playwright install chromium` and retry: %s",
            exc,
        )
        return 1
    except KeyboardInterrupt:
        LOGGER.warning("Ozon authorization cancelled by user")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
