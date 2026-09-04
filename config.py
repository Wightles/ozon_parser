"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from utils.exceptions import ConfigurationError


BASE_DIR = Path(__file__).resolve().parent


def _path_from_env(name: str, default: str) -> Path:
    """Return an absolute path while keeping relative settings project-local."""
    path = Path(os.getenv(name, default)).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def _int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _float_from_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def _bool_from_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, str(default)).strip().casefold()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings without validation tied to a specific entry point."""

    ozon_phone: str | None
    cookies_path: Path
    ozon_login_url: str
    ozon_cdp_url: str | None
    ozon_headless: bool
    ozon_navigation_timeout: float
    ozon_login_timeout: float
    ozon_manual_timeout: float
    ozon_http_connect_timeout: float
    ozon_http_read_timeout: float
    ozon_http_max_redirects: int
    ozon_user_agent: str | None
    gmail_credentials_path: Path
    gmail_token_path: Path
    gmail_ozon_query: str
    gmail_poll_interval: float
    gmail_timeout: float
    gmail_max_results: int
    results_dir: Path
    log_level: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str | None
    postgres_sslmode: str
    postgres_channel_binding: str

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        """Load settings from an optional dotenv file and the environment."""
        load_dotenv(dotenv_path=env_file or BASE_DIR / ".env")
        return cls(
            ozon_phone=os.getenv("OZON_PHONE") or None,
            cookies_path=_path_from_env("COOKIES_PATH", "cookies.json"),
            ozon_login_url=os.getenv(
                "OZON_LOGIN_URL", "https://www.ozon.ru/"
            ),
            ozon_cdp_url=os.getenv("OZON_CDP_URL") or None,
            ozon_headless=_bool_from_env("OZON_HEADLESS", False),
            ozon_navigation_timeout=_float_from_env(
                "OZON_NAVIGATION_TIMEOUT", 30.0
            ),
            ozon_login_timeout=_float_from_env("OZON_LOGIN_TIMEOUT", 120.0),
            ozon_manual_timeout=_float_from_env(
                "OZON_MANUAL_TIMEOUT", 300.0
            ),
            ozon_http_connect_timeout=_float_from_env(
                "OZON_HTTP_CONNECT_TIMEOUT", 10.0
            ),
            ozon_http_read_timeout=_float_from_env(
                "OZON_HTTP_READ_TIMEOUT", 30.0
            ),
            ozon_http_max_redirects=_int_from_env(
                "OZON_HTTP_MAX_REDIRECTS", 5
            ),
            ozon_user_agent=os.getenv("OZON_USER_AGENT") or None,
            gmail_credentials_path=_path_from_env(
                "GMAIL_CREDENTIALS_PATH", "credentials.json"
            ),
            gmail_token_path=_path_from_env("GMAIL_TOKEN_PATH", "token.json"),
            gmail_ozon_query=os.getenv(
                "GMAIL_OZON_QUERY", "from:(ozon.ru)"
            ),
            gmail_poll_interval=_float_from_env("GMAIL_POLL_INTERVAL", 5.0),
            gmail_timeout=_float_from_env("GMAIL_TIMEOUT", 120.0),
            gmail_max_results=_int_from_env("GMAIL_MAX_RESULTS", 10),
            results_dir=_path_from_env("RESULTS_DIR", "results"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
            postgres_port=_int_from_env("POSTGRES_PORT", 5432),
            postgres_db=os.getenv("POSTGRES_DB", "ozon_parser"),
            postgres_user=os.getenv("POSTGRES_USER", "ozon"),
            postgres_password=os.getenv("POSTGRES_PASSWORD") or None,
            postgres_sslmode=os.getenv("POSTGRES_SSLMODE", "prefer"),
            postgres_channel_binding=os.getenv(
                "POSTGRES_CHANNEL_BINDING", "prefer"
            ),
        )

    def require_ozon_phone(self) -> str:
        """Return the configured phone or fail with an actionable message."""
        if not self.ozon_phone:
            raise ConfigurationError("OZON_PHONE is not set; add it to .env")
        return self.ozon_phone

    def require_postgres_password(self) -> str:
        """Return the database password when a database operation needs it."""
        if not self.postgres_password:
            raise ConfigurationError(
                "POSTGRES_PASSWORD is not set; add it to .env"
            )
        return self.postgres_password


def get_settings(env_file: Path | None = None) -> Settings:
    """Build a fresh settings object, which also keeps tests isolated."""
    return Settings.from_env(env_file)
