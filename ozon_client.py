"""Authenticated HTTP client for downloading Ozon product pages."""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from collections.abc import Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from requests import Response, Session
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    HTTPError,
    RequestException,
    Timeout,
    TooManyRedirects,
)

from config import Settings, get_settings
from logging_config import configure_logging
from utils.exceptions import (
    CookiesExpiredError,
    CookiesInvalidError,
    CookiesNotFoundError,
    OzonAntiBotError,
    OzonParserError,
    ProductNotFoundError,
    ProductPageError,
)


LOGGER = logging.getLogger(__name__)
PRODUCT_URL_TEMPLATE = "https://www.ozon.ru/product/{sku}/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

LOGIN_URL_MARKERS = ("/login", "/signin", "/authorize", "/gocheckout")
LOGIN_TEXT_MARKERS = (
    "войти или зарегистрироваться",
    "введите номер телефона",
    "получить код",
    "sign in to ozon",
)
ANTI_BOT_TEXT_MARKERS = (
    "доступ ограничен",
    "подтвердите, что вы не робот",
    "проверка безопасности",
    "слишком много запросов",
    "access denied",
    "checking your browser",
    "too many requests",
)
NOT_FOUND_TEXT_MARKERS = (
    "такой страницы не существует",
    "страница не найдена",
    "товар не найден",
    "product not found",
)


class _VisibleTextExtractor(HTMLParser):
    """Extract user-visible text for page-type diagnostics, not product data."""

    _IGNORED_TAGS = frozenset({"head", "script", "style", "template"})

    def __init__(self, max_characters: int = 30_000) -> None:
        super().__init__(convert_charrefs=True)
        self.max_characters = max_characters
        self._chunks: list[str] = []
        self._characters = 0
        self._ignored_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if self._ignored_depth:
            self._ignored_depth += 1
        elif tag in self._IGNORED_TAGS:
            self._ignored_depth = 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del tag, attrs

    def handle_endtag(self, tag: str) -> None:
        del tag
        if self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or self._characters >= self.max_characters:
            return
        remaining = self.max_characters - self._characters
        chunk = data[:remaining]
        self._chunks.append(chunk)
        self._characters += len(chunk)

    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


def visible_page_text(html: str) -> str:
    """Return bounded visible text used only to classify error pages."""
    parser = _VisibleTextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def normalize_sku(raw_sku: str) -> str:
    """Validate the numeric SKU used in a product URL."""
    sku = raw_sku.strip()
    if not re.fullmatch(r"[0-9]+", sku):
        raise ProductPageError("Ozon SKU must contain digits only")
    return sku


def load_cookies(path: Path) -> list[dict[str, Any]]:
    """Load and validate Playwright cookies from JSON."""
    if not path.is_file():
        raise CookiesNotFoundError(
            f"Cookies file is missing: {path}. Run get_cookies.py first."
        )

    try:
        raw_cookies = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CookiesNotFoundError(f"Cannot read cookies file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CookiesInvalidError(
            f"Cookies file is not valid JSON: {path}"
        ) from exc

    if not isinstance(raw_cookies, list) or not raw_cookies:
        raise CookiesInvalidError("Cookies file must contain a non-empty list")

    cookies: list[dict[str, Any]] = []
    for index, raw_cookie in enumerate(raw_cookies):
        if not isinstance(raw_cookie, dict):
            raise CookiesInvalidError(f"Cookie #{index + 1} must be an object")

        name = raw_cookie.get("name")
        value = raw_cookie.get("value")
        domain = raw_cookie.get("domain")
        path_value = raw_cookie.get("path", "/")
        if not isinstance(name, str) or not name:
            raise CookiesInvalidError(f"Cookie #{index + 1} has no valid name")
        if not isinstance(value, str):
            raise CookiesInvalidError(f"Cookie #{index + 1} has no valid value")
        if not isinstance(domain, str) or not domain:
            raise CookiesInvalidError(f"Cookie #{index + 1} has no valid domain")
        normalized_domain = domain.lstrip(".").casefold()
        if normalized_domain != "ozon.ru" and not normalized_domain.endswith(
            ".ozon.ru"
        ):
            raise CookiesInvalidError(
                f"Cookie #{index + 1} belongs to a non-Ozon domain"
            )
        if not isinstance(path_value, str) or not path_value.startswith("/"):
            raise CookiesInvalidError(f"Cookie #{index + 1} has no valid path")
        expires = raw_cookie.get("expires")
        if expires is not None and (
            isinstance(expires, bool) or not isinstance(expires, (int, float))
        ):
            raise CookiesInvalidError(
                f"Cookie #{index + 1} has no valid expiration time"
            )
        cookies.append(dict(raw_cookie))
    return cookies


def apply_cookies(
    session: Session,
    cookies: list[Mapping[str, Any]],
    *,
    now: float | None = None,
) -> int:
    """Transfer active Playwright cookies into a requests session."""
    current_time = time.time() if now is None else now
    active_count = 0
    expired_count = 0

    for cookie in cookies:
        expires = cookie.get("expires")
        normalized_expires: int | None = None
        if isinstance(expires, (int, float)) and not isinstance(expires, bool):
            if expires > 0:
                normalized_expires = int(expires)
                if normalized_expires <= current_time:
                    expired_count += 1
                    continue

        rest: dict[str, Any] = {}
        if cookie.get("httpOnly") is True:
            rest["HttpOnly"] = True
        same_site = cookie.get("sameSite")
        if isinstance(same_site, str) and same_site:
            rest["SameSite"] = same_site

        session.cookies.set(
            str(cookie["name"]),
            str(cookie["value"]),
            domain=str(cookie["domain"]),
            path=str(cookie.get("path", "/")),
            secure=bool(cookie.get("secure", False)),
            expires=normalized_expires,
            rest=rest,
        )
        active_count += 1

    if active_count == 0:
        if expired_count:
            raise CookiesExpiredError(
                "All cookies have expired. Run get_cookies.py again."
            )
        raise CookiesInvalidError("No usable cookies were found")
    return active_count


class OzonClient:
    """Download authenticated product HTML through one persistent session."""

    def __init__(
        self,
        cookies_path: Path,
        *,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        max_redirects: int = 5,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Session | None = None,
    ) -> None:
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ProductPageError("HTTP timeouts must be greater than zero")
        if max_redirects <= 0:
            raise ProductPageError("HTTP max redirects must be greater than zero")
        if not user_agent.strip():
            raise ProductPageError("HTTP User-Agent must not be empty")

        self.timeout = (connect_timeout, read_timeout)
        self.session = session or requests.Session()
        self.session.max_redirects = max_redirects
        self.session.headers.update(DEFAULT_HEADERS)
        self.session.headers["User-Agent"] = user_agent.strip()

        cookies = load_cookies(cookies_path)
        cookie_count = apply_cookies(self.session, cookies)
        LOGGER.info("Loaded %d active Ozon cookies", cookie_count)

    @classmethod
    def from_settings(
        cls, settings: Settings, *, session: Session | None = None
    ) -> OzonClient:
        """Build the client from shared environment settings."""
        return cls(
            cookies_path=settings.cookies_path,
            connect_timeout=settings.ozon_http_connect_timeout,
            read_timeout=settings.ozon_http_read_timeout,
            max_redirects=settings.ozon_http_max_redirects,
            user_agent=settings.ozon_user_agent or DEFAULT_USER_AGENT,
            session=session,
        )

    def __enter__(self) -> OzonClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close pooled HTTP connections."""
        self.session.close()

    def get_product_html(self, sku: str) -> str:
        """Fetch and validate one Ozon product page without parsing its data."""
        normalized_sku = normalize_sku(sku)
        url = PRODUCT_URL_TEMPLATE.format(sku=normalized_sku)
        LOGGER.info("Fetching Ozon product page for SKU %s", normalized_sku)

        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
                allow_redirects=True,
            )
        except Timeout as exc:
            raise ProductPageError(
                f"Ozon request timed out for SKU {normalized_sku}"
            ) from exc
        except TooManyRedirects as exc:
            raise OzonAntiBotError(
                "Ozon returned too many redirects; cookies may be expired or "
                "anti-bot protection may be active"
            ) from exc
        except RequestsConnectionError as exc:
            raise ProductPageError(
                f"Cannot connect to Ozon for SKU {normalized_sku}"
            ) from exc
        except RequestException as exc:
            raise ProductPageError(
                f"Ozon request failed for SKU {normalized_sku}"
            ) from exc

        self._validate_response(response, normalized_sku)
        LOGGER.info("Ozon product page received for SKU %s", normalized_sku)
        return response.text

    @staticmethod
    def _validate_response(response: Response, sku: str) -> None:
        visible_text = visible_page_text(response.text).casefold()

        if response.status_code == 404 or any(
            marker in visible_text for marker in NOT_FOUND_TEXT_MARKERS
        ):
            raise ProductNotFoundError(f"Ozon product was not found for SKU {sku}")
        if response.status_code == 429 or any(
            marker in visible_text for marker in ANTI_BOT_TEXT_MARKERS
        ):
            raise OzonAntiBotError(
                "Ozon anti-bot or rate-limit page was returned. Retry later "
                "without attempting to bypass the protection."
            )

        final_url = urlparse(response.url)
        path = final_url.path.casefold()
        if final_url.hostname == "id.ozon.ru" or any(
            marker in path for marker in LOGIN_URL_MARKERS
        ):
            raise CookiesExpiredError(
                "Ozon redirected to a login page. Run get_cookies.py again."
            )
        login_marker_count = sum(
            marker in visible_text for marker in LOGIN_TEXT_MARKERS
        )
        if (
            LOGIN_TEXT_MARKERS[0] in visible_text
            or login_marker_count >= 2
        ):
            raise CookiesExpiredError(
                "Ozon returned a login page. Run get_cookies.py again."
            )

        try:
            response.raise_for_status()
        except HTTPError as exc:
            if response.status_code in {401, 403}:
                raise CookiesExpiredError(
                    "Ozon rejected the authenticated session. Refresh cookies."
                ) from exc
            raise ProductPageError(
                f"Ozon returned HTTP {response.status_code} for SKU {sku}"
            ) from exc

        hostname = (final_url.hostname or "").casefold()
        content_type = response.headers.get("Content-Type", "").casefold()
        if hostname not in {"ozon.ru", "www.ozon.ru"}:
            raise ProductPageError(
                f"Ozon request ended on an unexpected host for SKU {sku}"
            )
        if "/product/" not in path or sku not in path:
            raise ProductPageError(
                f"Ozon returned an unexpected page for SKU {sku}"
            )
        if "text/html" not in content_type:
            raise ProductPageError(
                f"Ozon returned non-HTML content for SKU {sku}"
            )
        if not response.text.strip():
            raise ProductPageError(f"Ozon returned an empty page for SKU {sku}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and validate one authenticated Ozon product page."
    )
    parser.add_argument(
        "sku",
        nargs="?",
        default="2359066702",
        help="Numeric Ozon SKU (default: 2359066702).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        settings = get_settings()
    except OzonParserError as exc:
        configure_logging()
        LOGGER.error("Cannot load application settings: %s", exc)
        return 1

    configure_logging(settings.log_level)
    try:
        with OzonClient.from_settings(settings) as client:
            html = client.get_product_html(args.sku)
    except OzonParserError as exc:
        LOGGER.error("Ozon HTTP diagnostic failed: %s", exc)
        return 1

    LOGGER.info(
        "Ozon HTTP diagnostic succeeded for SKU %s; received %d characters",
        args.sku,
        len(html),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
