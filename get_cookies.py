"""Authorize on data.ozon.ru with Playwright and save browser cookies."""

from __future__ import annotations

import argparse
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
    BrowserContext,
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
PUSH_APPROVAL_MARKERS = (
    "код из пуш-уведомления",
    "проверьте устройство, на котором вы авторизованы",
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
        "войти",
        "get code",
        "send code",
        "continue",
        "sign in",
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


def is_ozon_hostname(hostname: str | None) -> bool:
    """Return whether a hostname belongs to Ozon itself."""
    normalized = (hostname or "").rstrip(".").casefold()
    return normalized == "ozon.ru" or normalized.endswith(".ozon.ru")


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


def phone_for_ozon_id(raw_phone: str) -> str:
    """Return digits expected after Ozon ID's preselected Russian +7 code."""
    normalized = normalize_phone(raw_phone)
    digits = normalized.removeprefix("+")
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        return digits[1:]
    return digits


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
        if login_url.scheme != "https" or not is_ozon_hostname(
            login_url.hostname
        ):
            raise OzonLoginError(
                "OZON_LOGIN_URL must be an HTTPS URL on ozon.ru"
            )

        self.settings = settings
        self.gmail_client = gmail_client

    def authorize(
        self,
        cdp_url: str | None = None,
        *,
        capture_only: bool = False,
    ) -> None:
        """Run the browser login flow and persist the resulting cookies."""
        if capture_only and cdp_url is None:
            raise OzonLoginError("--capture-only requires --cdp-url")
        phone = (
            None
            if capture_only
            else phone_for_ozon_id(self.settings.require_ozon_phone())
        )
        LOGGER.info("Starting Ozon authorization")

        with sync_playwright() as playwright:
            browser = None
            owns_context = cdp_url is None
            if cdp_url is None:
                browser = playwright.chromium.launch(
                    headless=self.settings.ozon_headless
                )
                context = browser.new_context(locale="ru-RU")
                page = context.new_page()
                open_login_page = True
            else:
                LOGGER.info("Connecting to an existing Chrome session")
                browser = playwright.chromium.connect_over_cdp(cdp_url)
                context, page = self._find_ozon_page(browser.contexts)
                open_login_page = False

            context.set_default_timeout(
                self.settings.ozon_navigation_timeout * 1000
            )
            try:
                if capture_only:
                    self._save_context_cookies(context)
                else:
                    if phone is None:
                        raise OzonLoginError("Ozon phone number is unavailable")
                    self._authorize_context(
                        context,
                        page,
                        phone,
                        open_login_page=open_login_page,
                    )
            finally:
                if owns_context:
                    context.close()
                    browser.close()

    @staticmethod
    def _find_ozon_page(
        contexts: Sequence[BrowserContext],
    ) -> tuple[BrowserContext, Page]:
        """Select an already-open Ozon tab without inspecting other pages."""
        for context in contexts:
            for page in context.pages:
                if is_ozon_hostname(urlparse(page.url).hostname):
                    return context, page
        raise OzonLoginError(
            "The connected Chrome session has no open Ozon tab"
        )

    def _authorize_context(
        self,
        context: BrowserContext,
        page: Page,
        phone: str,
        *,
        open_login_page: bool,
    ) -> None:
        if open_login_page:
            self._open_login_page(page)

        phone_input = self._wait_for_phone_input(page)
        LOGGER.info("Ozon phone form detected")
        phone_input.fill(phone)

        requested_at = datetime.now(UTC)
        self._request_verification_code(page, phone_input)
        LOGGER.info("Verification code requested")

        code_controls = self._wait_for_code_control(page)
        if code_controls:
            code = self.gmail_client.wait_for_verification_code(requested_at)
            self._fill_verification_code(page, code_controls, code)
            self._submit_verification_code(page, code_controls)
        self._wait_for_authorization(page)

        self._save_context_cookies(context)

    def _save_context_cookies(self, context: BrowserContext) -> None:
        cookies = [
            cookie
            for cookie in context.cookies()
            if is_ozon_hostname(str(cookie.get("domain", "")).lstrip("."))
        ]
        save_cookies(cookies, self.settings.cookies_path)
        LOGGER.info("Authorization completed")
        LOGGER.info("Cookies saved to %s", self.settings.cookies_path)

    def _open_login_page(self, page: Page) -> None:
        LOGGER.info("Opening Ozon login page")
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
                "Timed out while opening the Ozon login page"
            ) from None
        except PlaywrightError as exc:
            message = str(exc).casefold()
            if "too_many_redirects" in message or "redirect" in message:
                raise OzonCaptchaError(
                    "Ozon returned a protective redirect loop. Retry in a visible "
                    "browser from a network accepted by Ozon."
                ) from exc
            raise OzonLoginError("Cannot open the Ozon login page") from exc

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
            page_text = self._page_text(page).casefold()
            if any(marker in page_text for marker in PUSH_APPROVAL_MARKERS):
                self._wait_for_push_approval(page)
                return []
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

            hostname = urlparse(page.url).hostname
            code_controls = self._find_code_controls(page)
            phone_input = self._find_input(page, "phone")
            looks_authorized = (
                is_ozon_hostname(hostname)
                and not code_controls
                and phone_input is None
                and not self._login_frame_visible(page)
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

    def _wait_for_push_approval(self, page: Page) -> None:
        if self.settings.ozon_headless:
            raise OzonLoginError(
                "Ozon requires confirmation in the mobile app; rerun with "
                "OZON_HEADLESS=false"
            )

        LOGGER.warning(
            "Ozon requires confirmation in the mobile app. Approve the login "
            "within %.0f seconds.",
            self.settings.ozon_manual_timeout,
        )
        deadline = time.monotonic() + self.settings.ozon_manual_timeout
        while time.monotonic() < deadline:
            if not self._login_frame_visible(page):
                LOGGER.info("Ozon mobile-app confirmation completed")
                return
            page.wait_for_timeout(1000)
        raise OzonLoginError(
            "Ozon mobile-app confirmation was not completed before the timeout"
        )

    @staticmethod
    def _login_frame_visible(page: Page) -> bool:
        for frame in page.frames:
            parsed = urlparse(frame.url)
            if not is_ozon_hostname(parsed.hostname):
                continue
            if parsed.path != "/ozonid-lite":
                continue
            try:
                if frame.locator("body").is_visible(timeout=1000):
                    return True
            except (PlaywrightError, PlaywrightTimeoutError):
                continue
        return False

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
        inputs = self._visible_inputs(page)
        scored = [
            (self._input_score(item, kind), index, control)
            for index, (control, item) in enumerate(inputs)
            if not item.disabled and not item.read_only
        ]
        scored = [candidate for candidate in scored if candidate[0] >= 40]
        if not scored:
            return None
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][2]

    def _find_code_controls(self, page: Page) -> list[Locator]:
        inputs = self._visible_inputs(page)
        single_input = self._find_input(page, "code")
        if single_input is not None:
            return [single_input]

        split_controls = [
            control
            for control, item in inputs
            if not item.disabled
            and not item.read_only
            and item.max_length == 1
            and (
                item.input_mode in {"numeric", "decimal"}
                or item.type in {"number", "tel", "text"}
            )
        ]
        if 4 <= len(split_controls) <= 8:
            return split_controls
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
        controls = self._visible_controls(
            page,
            'button:visible, [role="button"]:visible, '
            'input[type="submit"]:visible',
        )
        scored: list[tuple[int, int, Locator]] = []
        for index, (control, item) in enumerate(controls):
            if item.disabled:
                continue
            normalized_text = item.searchable_text
            visible_text = item.text.strip().casefold()
            score = max(
                (
                    200 - position
                    if visible_text == word
                    else 100 - position
                    for position, word in enumerate(_ACTION_WORDS[kind])
                    if word in normalized_text
                ),
                default=0,
            )
            if score:
                scored.append((score, index, control))

        if not scored:
            enabled = [
                control for control, item in controls if not item.disabled
            ]
            return enabled[0] if len(enabled) == 1 else None
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][2]

    def _visible_inputs(
        self, page: Page
    ) -> list[tuple[Locator, ControlMetadata]]:
        return self._visible_controls(page, "input:visible")

    def _visible_controls(
        self, page: Page, selector: str
    ) -> list[tuple[Locator, ControlMetadata]]:
        found: list[tuple[Locator, ControlMetadata]] = []
        for frame in page.frames:
            controls = frame.locator(selector)
            metadata = self._control_metadata(controls)
            found.extend(
                (controls.nth(item.index), item)
                for item in metadata
                if item.index >= 0
            )
        return found

    @staticmethod
    def _control_metadata(controls: Locator) -> list[ControlMetadata]:
        try:
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
        except PlaywrightError:
            # Ozon redirects during login; the polling loop will inspect the
            # controls again after Playwright creates the new page context.
            return []
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
        texts: list[str] = []
        for frame in page.frames:
            try:
                texts.append(frame.locator("body").inner_text(timeout=2000))
            except (PlaywrightError, PlaywrightTimeoutError):
                continue
        return "\n".join(texts)

    def _log_control_diagnostics(self, page: Page) -> None:
        inputs = self._visible_inputs(page)
        buttons = self._visible_controls(
            page,
            'button:visible, [role="button"]:visible, '
            'input[type="submit"]:visible',
        )
        LOGGER.error(
            "Login DOM diagnostics: url=%s visible_inputs=%d visible_buttons=%d",
            page.url,
            len(inputs),
            len(buttons),
        )


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line interface for browser authorization."""
    parser = argparse.ArgumentParser(
        description="Authorize in Ozon and save browser cookies."
    )
    parser.add_argument(
        "--cdp-url",
        help=(
            "connect to an already-running Chrome DevTools endpoint, for "
            "example http://127.0.0.1:9223"
        ),
    )
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="save Ozon cookies from an already-authorized CDP session",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
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
        authenticator.authorize(
            cdp_url=args.cdp_url,
            capture_only=args.capture_only,
        )
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
