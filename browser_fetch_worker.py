"""Fetch one page through local Chrome and exit without closing that browser."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", required=True, type=float)
    return parser.parse_args()


def is_ozon_hostname(hostname: str | None) -> bool:
    normalized = (hostname or "").rstrip(".").casefold()
    return normalized == "ozon.ru" or normalized.endswith(".ozon.ru")


def fetch_page(args: argparse.Namespace) -> dict[str, object]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.connect_over_cdp(args.cdp_url)

    page = next(
        (
            page
            for context in browser.contexts
            for page in context.pages
            if is_ozon_hostname(urlparse(page.url).hostname)
        ),
        None,
    )
    if page is None:
        raise RuntimeError("The Chrome session has no open Ozon tab")

    response = page.goto(
        args.url,
        wait_until="domcontentloaded",
        timeout=args.timeout * 1000,
    )
    if response is None:
        raise RuntimeError("Chrome returned no navigation response")
    page.wait_for_timeout(3000)

    return {
        "status_code": response.status,
        "url": page.url,
        "content_type": response.headers.get("content-type", ""),
        "html": page.content(),
    }


def main() -> None:
    args = parse_arguments()
    try:
        payload = fetch_page(args)
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}\n")
        sys.stderr.flush()
        os._exit(1)

    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
