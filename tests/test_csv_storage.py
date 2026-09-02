"""Tests for UTF-8-SIG CSV serialization."""

from __future__ import annotations

import csv
from pathlib import Path

from models.product import Product
from storage.csv_storage import CSV_FIELDS, CsvProductStorage


def test_saves_header_and_product_row(
    tmp_path: Path,
    sample_product: Product,
) -> None:
    path = tmp_path / "nested" / "products.csv"

    row_count = CsvProductStorage(path).save([sample_product])

    assert row_count == 1
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        assert tuple(reader.fieldnames or ()) == CSV_FIELDS

    assert len(rows) == 1
    assert rows[0] == {
        "sku": "2359066702",
        "title": "Тестовый товар",
        "price": "1999.50",
        "rating": "4.8",
        "reviews_total": "1234",
        "cover_image": "https://cdn.example/cover.jpg",
        "photos_seller": "2",
        "videos_seller": "0",
        "color": "Красный",
        "material": "",
        "art_set": "ABC-123",
        "has_rich_content": "true",
        "parsed_at": "2026-09-02T03:04:05+00:00",
    }


def test_empty_snapshot_still_contains_header(tmp_path: Path) -> None:
    path = tmp_path / "products.csv"

    row_count = CsvProductStorage(path).save([])

    assert row_count == 0
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert rows == [list(CSV_FIELDS)]
