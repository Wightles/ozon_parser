"""Tests for conversion of JSON-LD into the Product model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from parsers.product_parser import ProductParser
from utils.exceptions import ProductParseError


def test_parses_product_and_deduplicates_images(
    product_json_ld_html: str,
    parsed_at: datetime,
) -> None:
    product = ProductParser().parse(
        product_json_ld_html,
        "2359066702",
        characteristics=[
            {"name": "Цвет", "value": "Красный"},
            ("Артикул производителя", "ABC-123"),
        ],
        parsed_at=parsed_at,
    )

    assert product.sku == "2359066702"
    assert product.title == "Тестовый товар"
    assert product.price == Decimal("1999.50")
    assert product.rating == 4.8
    assert product.reviews_total == 1234
    assert product.cover_image == "https://cdn.example/cover.jpg"
    assert product.photos_seller == 2
    assert product.videos_seller == 0
    assert product.color == "Красный"
    assert product.material is None
    assert product.art_set == "ABC-123"
    assert product.has_rich_content is True
    assert product.parsed_at == parsed_at


def test_optional_fields_do_not_break_parser() -> None:
    html = """
    <script type="application/ld+json">
    {"@type": "Product", "sku": "2829800382"}
    </script>
    """

    product = ProductParser().parse(html, "2829800382")

    assert product.title is None
    assert product.price is None
    assert product.rating is None
    assert product.reviews_total is None
    assert product.cover_image is None
    assert product.photos_seller == 0
    assert product.has_rich_content is False


def test_missing_product_json_raises_clear_error() -> None:
    with pytest.raises(ProductParseError, match="JSON-LD Product was not found"):
        ProductParser().parse("<html></html>", "2359066702")
