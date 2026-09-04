"""Non-mutating diagnostics for the configured Ozon parser environment."""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from config import get_settings
from logging_config import configure_logging
from ozon_client import apply_cookies, load_cookies
from storage.postgres_storage import PostgresProductStorage
from utils.exceptions import OzonParserError, recovery_hint

if TYPE_CHECKING:
    from config import Settings


LOGGER = logging.getLogger(__name__)

REQUIRED_SCHEMA_OBJECTS = (
    "products",
    "product_history",
    "datalens_products",
    "datalens_product_history",
)

SCHEMA_OBJECTS_SQL = """
SELECT c.relname
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname = ANY(%s)
  AND c.relkind IN ('r', 'p', 'v', 'm')
""".strip()


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Single diagnostic outcome suitable for logs and tests."""

    name: str
    ok: bool
    message: str


def _ok(name: str, message: str) -> CheckResult:
    return CheckResult(name=name, ok=True, message=message)


def _fail(name: str, message: str) -> CheckResult:
    return CheckResult(name=name, ok=False, message=message)


def _failure_result(name: str, error: OzonParserError) -> CheckResult:
    message = str(error)
    hint = recovery_hint(error)
    if hint:
        message = f"{message}. Next step: {hint}"
    return _fail(name, message)


def _path_label(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def check_file(name: str, path: Path) -> CheckResult:
    """Verify that a required local file exists."""
    if path.is_file():
        return _ok(name, f"found {_path_label(path)}")
    return _fail(name, f"missing {_path_label(path)}")


def check_cookies(settings: Settings) -> CheckResult:
    """Verify that saved Ozon cookies are readable and currently usable."""
    try:
        cookies = load_cookies(settings.cookies_path)
        active_count = apply_cookies(_CookieCheckSession(), cookies)
    except OzonParserError as exc:
        return _failure_result("Ozon cookies", exc)
    return _ok("Ozon cookies", f"{active_count} active cookie(s)")


def check_database(settings: Settings) -> CheckResult:
    """Verify PostgreSQL connectivity and required DataLens schema objects."""
    try:
        with PostgresProductStorage.from_settings(settings) as storage:
            storage.check_connection()
            missing_objects = _missing_schema_objects(storage.connection)
    except OzonParserError as exc:
        return _failure_result("PostgreSQL", exc)
    except Exception as exc:
        return _fail("PostgreSQL", f"schema check failed: {exc}")

    if missing_objects:
        return _fail(
            "PostgreSQL",
            "missing schema object(s): " + ", ".join(missing_objects),
        )
    return _ok("PostgreSQL", "connection and DataLens views are ready")


def _missing_schema_objects(connection: Any) -> tuple[str, ...]:
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_OBJECTS_SQL, (list(REQUIRED_SCHEMA_OBJECTS),))
        rows = cursor.fetchall()
    connection.commit()
    found = {str(row[0]) for row in rows if row and row[0] is not None}
    return tuple(name for name in REQUIRED_SCHEMA_OBJECTS if name not in found)


def run_checks(
    settings: Settings,
    *,
    include_database: bool = True,
) -> tuple[CheckResult, ...]:
    """Run configured diagnostics without downloading Ozon product pages."""
    results = [
        check_file("Gmail credentials", settings.gmail_credentials_path),
        check_file("Gmail token", settings.gmail_token_path),
        check_cookies(settings),
    ]
    if include_database:
        results.append(check_database(settings))
    return tuple(results)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check local Ozon parser configuration without parsing."
    )
    parser.add_argument(
        "--skip-database",
        action="store_true",
        help="skip PostgreSQL connectivity and schema checks",
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
    results = run_checks(settings, include_database=not args.skip_database)
    for result in results:
        level = logging.INFO if result.ok else logging.ERROR
        LOGGER.log(level, "%s: %s", result.name, result.message)
    return 0 if all(result.ok for result in results) else 1


class _CookieCheckSession:
    """Minimal object with a requests-like cookie jar for apply_cookies."""

    def __init__(self) -> None:
        import requests

        self.cookies = requests.Session().cookies


if __name__ == "__main__":
    raise SystemExit(main())
