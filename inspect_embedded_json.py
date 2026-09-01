"""Inspect embedded JSON metadata without printing product or session data."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config import get_settings
from logging_config import configure_logging
from ozon_client import OzonClient
from parsers.embedded_json import (
    EmbeddedJsonBlock,
    extract_embedded_json,
    find_json_ld_product,
    find_json_ld_products,
)
from utils.exceptions import OzonParserError, ProductParseError


LOGGER = logging.getLogger(__name__)
DEFAULT_SKU = "2359066702"
PRODUCT_FIELDS = (
    "sku",
    "name",
    "brand",
    "offers",
    "aggregateRating",
    "image",
    "description",
)


def _describe_data(data: Any) -> str:
    if isinstance(data, Mapping):
        keys = sorted(str(key) for key in data)[:20]
        suffix = ", ..." if len(data) > len(keys) else ""
        return f"object keys=[{', '.join(keys)}{suffix}]"
    if isinstance(data, list):
        return f"array length={len(data)}"
    return type(data).__name__


def _describe_block(index: int, block: EmbeddedJsonBlock) -> str:
    identifier = block.identifier or "-"
    mime_type = block.mime_type or "-"
    return (
        f"JSON block {index}: source={block.source}, id={identifier}, "
        f"type={mime_type}, {_describe_data(block.data)}"
    )


def inspect_html(html: str, *, sku: str) -> None:
    """Log structure only, avoiding JSON values that may contain user data."""
    result = extract_embedded_json(html)
    if not result.blocks:
        raise ProductParseError("No supported embedded JSON blocks were found")

    LOGGER.info("Found %d supported JSON blocks", len(result.blocks))
    for index, block in enumerate(result.blocks, start=1):
        LOGGER.info(_describe_block(index, block))

    for issue in result.issues:
        LOGGER.warning(
            "Skipped invalid JSON: source=%s, id=%s, reason=%s",
            issue.source,
            issue.identifier or "-",
            issue.message,
        )

    products = find_json_ld_products(result.blocks)
    LOGGER.info("Found %d JSON-LD Product nodes", len(products))
    product = find_json_ld_product(result.blocks, sku=sku)
    if product is None:
        LOGGER.warning("JSON-LD Product for SKU %s was not found", sku)
        return

    present_fields = [field for field in PRODUCT_FIELDS if field in product]
    LOGGER.info(
        "JSON-LD Product for SKU %s contains fields: %s",
        sku,
        ", ".join(present_fields) or "none",
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect supported JSON structures in an Ozon product page"
    )
    parser.add_argument("sku", nargs="?", default=DEFAULT_SKU)
    parser.add_argument(
        "--html",
        type=Path,
        help="read a previously saved HTML file instead of requesting Ozon",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        if args.html is not None:
            html = args.html.read_text(encoding="utf-8")
        else:
            with OzonClient.from_settings(settings) as client:
                html = client.get_product_html(args.sku)
        inspect_html(html, sku=args.sku)
    except (OSError, UnicodeError, OzonParserError) as exc:
        LOGGER.error("Embedded JSON inspection failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
