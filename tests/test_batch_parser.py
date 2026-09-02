"""Tests for per-SKU failure isolation in the batch parser."""

from __future__ import annotations

from models.product import Product
from parse_ozon import parse_products
from utils.exceptions import ProductPageError


class FakeClient:
    def get_product_html(self, sku: str) -> str:
        if sku == "2829800382":
            raise ProductPageError("synthetic failure")
        return f"html:{sku}"


class FakeParser:
    def __init__(self, product: Product) -> None:
        self.product = product

    def parse(self, html: str, sku: str) -> Product:
        assert html == f"html:{sku}"
        return self.product


def test_continues_after_one_sku_fails(sample_product: Product) -> None:
    result = parse_products(
        ["2359066702", "2829800382"],
        FakeClient(),
        FakeParser(sample_product),
    )

    assert result.products == (sample_product,)
    assert result.failed_skus == ("2829800382",)
    assert result.has_failures is True
