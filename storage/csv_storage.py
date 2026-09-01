"""CSV persistence for normalized product snapshots."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from models.product import Product
from utils.exceptions import StorageError


CSV_FIELDS = (
    "sku",
    "title",
    "price",
    "rating",
    "reviews_total",
    "cover_image",
    "photos_seller",
    "videos_seller",
    "color",
    "material",
    "art_set",
    "has_rich_content",
    "parsed_at",
)


def _csv_value(value: Any) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


def product_to_csv_row(product: Product) -> dict[str, str | int | float]:
    """Serialize one product using stable column names and scalar values."""
    return {
        field: _csv_value(getattr(product, field))
        for field in CSV_FIELDS
    }


class CsvProductStorage:
    """Overwrite a CSV snapshot with a header and one row per product."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, products: Iterable[Product]) -> int:
        """Write products as UTF-8 with BOM and return the row count."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
                writer.writeheader()
                row_count = 0
                for product in products:
                    writer.writerow(product_to_csv_row(product))
                    row_count += 1
        except (OSError, csv.Error) as exc:
            raise StorageError(f"Cannot save products CSV: {self.path}") from exc
        return row_count


def save_products_csv(products: Iterable[Product], path: Path) -> int:
    """Convenience wrapper for writing a product CSV snapshot."""
    return CsvProductStorage(path).save(products)
