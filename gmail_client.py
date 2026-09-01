"""Gmail API client for reading fresh Ozon verification codes."""

from __future__ import annotations

import argparse
import base64
import logging
import re
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import get_settings
from logging_config import configure_logging
from utils.exceptions import (
    GmailApiError,
    GmailAuthenticationError,
    GmailCodeNotFoundError,
    GmailMessageNotFoundError,
    OzonParserError,
)

if TYPE_CHECKING:
    from config import Settings


LOGGER = logging.getLogger(__name__)
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = (GMAIL_READONLY_SCOPE,)
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})

_CODE_LABEL = (
    r"(?:"
    r"(?:одноразовый\s+)?код"
    r"(?:\s+(?:подтверждения|для\s+входа|из\s+письма))?"
    r"(?:\s+(?:в|на)\s+(?:личный\s+кабинет\s+)?"
    r"(?:ozon|озон)(?:\s+id)?)?"
    r"|verification\s+code|confirmation\s+code|login\s+code|security\s+code"
    r")"
)
_CODE_PATTERNS = (
    re.compile(
        rf"{_CODE_LABEL}\s*(?:[:\-—–]|\bis\b)?\s*"
        r"(?<![0-9])([0-9]{4,8})(?![0-9])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![0-9])([0-9]{4,8})(?![0-9])\s*(?:[:\-—–]\s*)?"
        rf"{_CODE_LABEL}",
        re.IGNORECASE,
    ),
)


class _HTMLTextExtractor(HTMLParser):
    """Convert a small email HTML document to searchable plain text."""

    _IGNORED_TAGS = frozenset({"head", "script", "style"})
    _SEPARATOR_TAGS = frozenset(
        {"br", "div", "li", "p", "table", "td", "th", "tr"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if self._ignored_depth:
            self._ignored_depth += 1
        elif tag in self._IGNORED_TAGS:
            self._ignored_depth = 1
        elif tag in self._SEPARATOR_TAGS:
            self._chunks.append(" ")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if not self._ignored_depth and tag in self._SEPARATOR_TAGS:
            self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_depth:
            self._ignored_depth -= 1
        elif tag in self._SEPARATOR_TAGS:
            self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join("".join(self._chunks).split())


def html_to_text(value: str) -> str:
    """Return readable text from an HTML email body."""
    parser = _HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.get_text()


def extract_verification_code(text: str) -> str:
    """Extract a context-bound 4-8 digit verification code."""
    normalized = " ".join(text.replace("\xa0", " ").split())
    for pattern in _CODE_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return match.group(1)
    raise GmailCodeNotFoundError(
        "A fresh Ozon email was found, but no verification code was detected"
    )


def _decode_base64url(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    try:
        decoded = base64.urlsafe_b64decode(data + padding)
    except (ValueError, TypeError) as exc:
        raise GmailApiError("Gmail returned a malformed message body") from exc
    return decoded.decode("utf-8", errors="replace")


def _iter_payload_parts(payload: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield payload
    parts = payload.get("parts", [])
    if not isinstance(parts, list):
        return
    for part in parts:
        if isinstance(part, Mapping):
            yield from _iter_payload_parts(part)


def _header_value(payload: Mapping[str, Any], name: str) -> str:
    headers = payload.get("headers", [])
    if not isinstance(headers, list):
        return ""
    for header in headers:
        if not isinstance(header, Mapping):
            continue
        header_name = header.get("name")
        header_value = header.get("value")
        if (
            isinstance(header_name, str)
            and header_name.casefold() == name.casefold()
            and isinstance(header_value, str)
        ):
            return header_value
    return ""


def message_text(message: Mapping[str, Any]) -> str:
    """Decode searchable headers, snippet and MIME bodies from a Gmail message."""
    payload = message.get("payload", {})
    if not isinstance(payload, Mapping):
        payload = {}

    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in _iter_payload_parts(payload):
        mime_type = part.get("mimeType")
        body = part.get("body", {})
        if not isinstance(mime_type, str) or not isinstance(body, Mapping):
            continue
        data = body.get("data")
        if not isinstance(data, str) or not data:
            continue
        decoded = _decode_base64url(data)
        if mime_type.casefold() == "text/plain":
            plain_parts.append(decoded)
        elif mime_type.casefold() == "text/html":
            html_parts.append(html_to_text(decoded))

    snippet = message.get("snippet")
    searchable_parts = [
        _header_value(payload, "Subject"),
        snippet if isinstance(snippet, str) else "",
        *plain_parts,
        *html_parts,
    ]
    return "\n".join(part for part in searchable_parts if part)


class GmailClient:
    """Authenticate with Gmail and poll for a fresh Ozon verification email."""

    def __init__(
        self,
        credentials_path: Path,
        token_path: Path,
        *,
        search_query: str = "from:(ozon.ru)",
        poll_interval: float = 5.0,
        timeout: float = 120.0,
        max_results: int = 10,
        service: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not search_query.strip():
            raise ValueError("Gmail search query must not be empty")
        if poll_interval <= 0:
            raise ValueError("Gmail poll interval must be greater than zero")
        if timeout <= 0:
            raise ValueError("Gmail timeout must be greater than zero")
        if max_results <= 0:
            raise ValueError("Gmail max results must be greater than zero")

        self.credentials_path = credentials_path
        self.token_path = token_path
        self.search_query = search_query.strip()
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.max_results = max_results
        self._service = service
        self._sleep = sleep
        self._monotonic = monotonic

    @classmethod
    def from_settings(
        cls, settings: Settings, *, service: Any | None = None
    ) -> GmailClient:
        """Build a client from the shared application settings."""
        return cls(
            credentials_path=settings.gmail_credentials_path,
            token_path=settings.gmail_token_path,
            search_query=settings.gmail_ozon_query,
            poll_interval=settings.gmail_poll_interval,
            timeout=settings.gmail_timeout,
            max_results=settings.gmail_max_results,
            service=service,
        )

    def authenticate(self) -> Any:
        """Create and cache an authorized Gmail API service."""
        if self._service is not None:
            return self._service

        credentials: Credentials | None = None
        if self.token_path.exists():
            LOGGER.info("Loading saved Gmail OAuth token")
            try:
                credentials = Credentials.from_authorized_user_file(
                    str(self.token_path), SCOPES
                )
            except (OSError, ValueError) as exc:
                raise GmailAuthenticationError(
                    f"Cannot load Gmail OAuth token from {self.token_path}"
                ) from exc

        try:
            if credentials and credentials.expired and credentials.refresh_token:
                LOGGER.info("Refreshing Gmail OAuth token")
                credentials.refresh(Request())
                self._save_token(credentials)
            elif not credentials or not credentials.valid:
                credentials = self._run_oauth_flow()
                self._save_token(credentials)

            self._service = build(
                "gmail",
                "v1",
                credentials=credentials,
                cache_discovery=False,
            )
        except GmailAuthenticationError:
            raise
        except (GoogleAuthError, OSError, ValueError) as exc:
            raise GmailAuthenticationError(
                "Gmail OAuth authentication failed"
            ) from exc

        LOGGER.info("Gmail API authorization completed")
        return self._service

    def wait_for_verification_code(
        self,
        received_after: datetime,
        *,
        timeout: float | None = None,
    ) -> str:
        """Poll Gmail for an Ozon code received after the supplied timestamp."""
        if received_after.tzinfo is None or received_after.utcoffset() is None:
            raise ValueError("received_after must be timezone-aware")

        effective_timeout = self.timeout if timeout is None else timeout
        if effective_timeout <= 0:
            raise ValueError("Gmail timeout must be greater than zero")

        service = self.authenticate()
        deadline = self._monotonic() + effective_timeout
        received_after_ms = int(received_after.timestamp() * 1000)
        query_after = max(0, int(received_after.timestamp()) - 1)
        query = f"{{{self.search_query}}} after:{query_after}"
        seen_message_ids: set[str] = set()
        found_message_without_code = False
        last_api_error: GmailApiError | None = None

        LOGGER.info("Waiting for a fresh Ozon verification email")
        while True:
            try:
                references = self._list_messages(service, query)
                last_api_error = None
                for reference in references:
                    message_id = reference.get("id")
                    if (
                        not isinstance(message_id, str)
                        or message_id in seen_message_ids
                    ):
                        continue
                    message = self._get_message(service, message_id)
                    seen_message_ids.add(message_id)
                    if self._internal_date_ms(message) < received_after_ms:
                        continue

                    found_message_without_code = True
                    try:
                        code = extract_verification_code(message_text(message))
                    except GmailCodeNotFoundError:
                        LOGGER.warning(
                            "A fresh Ozon email did not contain a recognizable code"
                        )
                        continue

                    LOGGER.info(
                        "Fresh Gmail verification message received; code extracted"
                    )
                    return code
            except GmailApiError as exc:
                if not exc.retryable:
                    raise
                last_api_error = exc
                LOGGER.warning("Temporary Gmail API error; polling will retry")

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            self._sleep(min(self.poll_interval, remaining))

        if found_message_without_code:
            raise GmailCodeNotFoundError(
                "Fresh Ozon email(s) arrived, but no verification code was found"
            )
        if last_api_error is not None:
            raise last_api_error
        raise GmailMessageNotFoundError(
            "No fresh Ozon verification email arrived before the timeout"
        )

    def _run_oauth_flow(self) -> Credentials:
        if not self.credentials_path.is_file():
            raise GmailAuthenticationError(
                "Gmail OAuth credentials file is missing: "
                f"{self.credentials_path}. Download a Desktop app client as "
                "credentials.json from Google Cloud Console."
            )

        LOGGER.info("Starting Gmail OAuth authorization in the browser")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path), SCOPES
            )
            return flow.run_local_server(port=0)
        except (GoogleAuthError, OSError, ValueError) as exc:
            raise GmailAuthenticationError(
                "Interactive Gmail OAuth authorization failed"
            ) from exc

    def _save_token(self, credentials: Credentials) -> None:
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
            self.token_path.chmod(0o600)
        except OSError as exc:
            raise GmailAuthenticationError(
                f"Cannot save Gmail OAuth token to {self.token_path}"
            ) from exc
        LOGGER.info("Gmail OAuth token saved")

    def _list_messages(
        self, service: Any, query: str
    ) -> list[Mapping[str, Any]]:
        try:
            response = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=self.max_results,
                    includeSpamTrash=True,
                )
                .execute()
            )
        except HttpError as exc:
            raise self._translate_http_error(exc) from exc
        except (GoogleAuthError, OSError) as exc:
            raise GmailApiError(
                "Temporary failure while contacting Gmail API", retryable=True
            ) from exc

        if not isinstance(response, Mapping):
            raise GmailApiError("Gmail returned an unexpected message list")
        messages = response.get("messages", [])
        if not isinstance(messages, list):
            raise GmailApiError("Gmail returned an unexpected message list")
        return [message for message in messages if isinstance(message, Mapping)]

    def _get_message(self, service: Any, message_id: str) -> Mapping[str, Any]:
        try:
            message = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as exc:
            raise self._translate_http_error(exc) from exc
        except (GoogleAuthError, OSError) as exc:
            raise GmailApiError(
                "Temporary failure while contacting Gmail API", retryable=True
            ) from exc

        if not isinstance(message, Mapping):
            raise GmailApiError("Gmail returned an unexpected message payload")
        return message

    @staticmethod
    def _internal_date_ms(message: Mapping[str, Any]) -> int:
        raw_value = message.get("internalDate")
        try:
            return int(raw_value)
        except (TypeError, ValueError) as exc:
            raise GmailApiError("Gmail message has no valid internal date") from exc

    @staticmethod
    def _translate_http_error(error: HttpError) -> GmailApiError:
        status = getattr(error.resp, "status", None)
        retryable = status in RETRYABLE_HTTP_STATUSES
        status_suffix = f" (HTTP {status})" if status is not None else ""
        return GmailApiError(
            f"Gmail API request failed{status_suffix}", retryable=retryable
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait for a fresh Ozon verification email via Gmail API."
    )
    parser.add_argument(
        "--auth-only",
        action="store_true",
        help="Complete OAuth and verify Gmail API initialization, then exit.",
    )
    parser.add_argument(
        "--lookback-seconds",
        type=float,
        default=0.0,
        help="Also accept messages received this many seconds before startup.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Override GMAIL_TIMEOUT for this diagnostic run.",
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

    if args.lookback_seconds < 0:
        LOGGER.error("--lookback-seconds must not be negative")
        return 2
    if args.timeout is not None and args.timeout <= 0:
        LOGGER.error("--timeout must be greater than zero")
        return 2

    started_at = datetime.now(UTC) - timedelta(seconds=args.lookback_seconds)
    try:
        client = GmailClient.from_settings(settings)
        if args.auth_only:
            client.authenticate()
            LOGGER.info("Gmail OAuth diagnostic succeeded")
            return 0
        code = client.wait_for_verification_code(
            started_at, timeout=args.timeout
        )
    except (OzonParserError, ValueError) as exc:
        LOGGER.error("Gmail diagnostic failed: %s", exc)
        return 1

    LOGGER.info("Gmail diagnostic succeeded; extracted a %d-digit code", len(code))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
