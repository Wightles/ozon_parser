"""Batch entry point for downloading Ozon products and exporting CSV."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from models.product import Product
from parsers.product_parser import ProductParser
from storage.csv_storage import CsvProductStorage
from storage.postgres_storage import PostgresProductStorage
from utils.exceptions import OzonParserError

if TYPE_CHECKING:
    from config import Settings


LOGGER = logging.getLogger(__name__)
CSV_FILENAME = "products.csv"


class ProductHtmlClient(Protocol):
    def get_product_html(self, sku: str) -> str:
        """Return product HTML for the supplied SKU."""
        ...


class HtmlProductParser(Protocol):
    def parse(self, html: str, sku: str) -> Product:
        """Return a normalized product from HTML."""
        ...


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Successful products and SKU values that failed independently."""

    products: tuple[Product, ...]
    failed_skus: tuple[str, ...]

    @property
    def has_failures(self) -> bool:
        return bool(self.failed_skus)


def parse_products(
    skus: Iterable[str],
    client: ProductHtmlClient,
    parser: HtmlProductParser,
) -> BatchResult:
    """Parse every SKU while isolating failures to the current item."""
    products: list[Product] = []
    failed_skus: list[str] = []

    for sku in skus:
        LOGGER.info("Parsing SKU %s", sku)
        try:
            html = client.get_product_html(sku)
            product = parser.parse(html, sku)
        except Exception as exc:
            LOGGER.error("Failed to parse SKU %s: %s", sku, exc)
            failed_skus.append(sku)
            continue

        products.append(product)
        LOGGER.info("SKU %s parsed successfully", sku)

    return BatchResult(
        products=tuple(products),
        failed_skus=tuple(failed_skus),
    )


def run_batch(
    *,
    skus: Sequence[str],
    client: ProductHtmlClient,
    output_path: Path,
    parser: HtmlProductParser | None = None,
) -> BatchResult:
    """Parse a batch and always save the successfully parsed products."""
    result = parse_products(skus, client, parser or ProductParser())
    row_count = CsvProductStorage(output_path).save(result.products)
    LOGGER.info("Saved %d products to %s", row_count, output_path)
    return result


def run_configured_batch(settings: Settings) -> BatchResult:
    """Run the complete CSV and PostgreSQL pipeline from shared settings."""
    from ozon_client import create_ozon_client

    output_path = settings.results_dir / CSV_FILENAME
    with create_ozon_client(settings) as client:
        result = run_batch(
            skus=settings.ozon_skus,
            client=client,
            output_path=output_path,
        )

    with PostgresProductStorage.from_settings(settings) as storage:
        storage.initialize_schema()
        row_count = storage.save(result.products)
    LOGGER.info(
        "Saved %d current products and history rows to PostgreSQL",
        row_count,
    )
    return result


def main() -> int:
    """Run the configured authenticated batch parser."""
    from config import get_settings
    from logging_config import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level)
    output_path = settings.results_dir / CSV_FILENAME
    LOGGER.info("Starting Ozon product parser")

    try:
        result = run_configured_batch(settings)
    except OzonParserError as exc:
        LOGGER.error(
            "Ozon pipeline failed; any written CSV remains at %s: %s",
            output_path,
            exc,
        )
        return 1

    if result.has_failures:
        LOGGER.warning(
            "Batch completed with %d failed SKU values",
            len(result.failed_skus),
        )
        return 1

    LOGGER.info("Ozon product parser completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
