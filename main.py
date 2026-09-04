"""Unified command-line entry point for the Ozon parser project."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the small dispatcher CLI used by local and scheduled runs."""
    parser = argparse.ArgumentParser(
        description="Run Ozon parser workflows from a single entry point."
    )
    subparsers = parser.add_subparsers(dest="command")

    parse_parser = subparsers.add_parser(
        "parse",
        help="parse configured Ozon SKU values and save CSV/PostgreSQL output",
    )
    parse_parser.add_argument(
        "--sku",
        action="append",
        default=[],
        help=(
            "override OZON_SKUS for this run; can be repeated or "
            "comma-separated"
        ),
    )
    parse_parser.add_argument(
        "--csv-only",
        action="store_true",
        help="write results/products.csv and skip PostgreSQL writes",
    )

    auth_parser = subparsers.add_parser(
        "auth",
        help="authorize in Ozon and save browser cookies",
    )
    auth_parser.add_argument(
        "--cdp-url",
        help=(
            "connect to an already-running Chrome DevTools endpoint, for "
            "example http://127.0.0.1:9223"
        ),
    )
    auth_parser.add_argument(
        "--capture-only",
        action="store_true",
        help="save Ozon cookies from an already-authorized CDP session",
    )

    gmail_parser = subparsers.add_parser(
        "gmail",
        help="check Gmail OAuth or wait for a fresh Ozon verification code",
    )
    gmail_parser.add_argument(
        "--auth-only",
        action="store_true",
        help="complete OAuth and verify Gmail API initialization, then exit",
    )
    gmail_parser.add_argument(
        "--lookback-seconds",
        type=float,
        default=0.0,
        help="also accept messages received this many seconds before startup",
    )
    gmail_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="override GMAIL_TIMEOUT for this diagnostic run",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check local files and PostgreSQL without parsing Ozon pages",
    )
    doctor_parser.add_argument(
        "--skip-database",
        action="store_true",
        help="skip PostgreSQL connectivity and schema checks",
    )
    return parser


def _run_module_main(module_name: str, argv: Sequence[str]) -> int:
    """Run another entry point while letting it own its argparse contract."""
    module = importlib.import_module(module_name)
    previous_argv = sys.argv
    sys.argv = [f"{module_name}.py", *argv]
    try:
        return int(module.main())
    finally:
        sys.argv = previous_argv


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch to the parser, Ozon authorization or Gmail diagnostics."""
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if args.command in {None, "parse"}:
        forwarded_args = []
        for sku in getattr(args, "sku", []):
            forwarded_args.extend(["--sku", sku])
        if getattr(args, "csv_only", False):
            forwarded_args.append("--csv-only")
        return _run_module_main("parse_ozon", forwarded_args)

    if args.command == "auth":
        forwarded_args: list[str] = []
        if args.cdp_url:
            forwarded_args.extend(["--cdp-url", args.cdp_url])
        if args.capture_only:
            forwarded_args.append("--capture-only")
        return _run_module_main("get_cookies", forwarded_args)

    if args.command == "gmail":
        forwarded_args = []
        if args.auth_only:
            forwarded_args.append("--auth-only")
        if args.lookback_seconds:
            forwarded_args.extend(
                ["--lookback-seconds", str(args.lookback_seconds)]
            )
        if args.timeout is not None:
            forwarded_args.extend(["--timeout", str(args.timeout)])
        return _run_module_main("gmail_client", forwarded_args)

    if args.command == "doctor":
        forwarded_args = []
        if args.skip_database:
            forwarded_args.append("--skip-database")
        return _run_module_main("healthcheck", forwarded_args)

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
