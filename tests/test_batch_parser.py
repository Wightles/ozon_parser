"""Tests for per-SKU failure isolation in the batch parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from models.product import Product
from parse_ozon import parse_products, run_configured_batch
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


class ContextClient:
    def __init__(self, html_by_sku: dict[str, str]) -> None:
        self.html_by_sku = html_by_sku
        self.requested_skus: list[str] = []

    def get_product_html(self, sku: str) -> str:
        self.requested_skus.append(sku)
        return self.html_by_sku[sku]

    def __enter__(self) -> ContextClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class CapturingStorage:
    saved_products: tuple[Product, ...] = ()
    initialized = False

    @classmethod
    def from_settings(cls, _settings: object) -> CapturingStorage:
        return cls()

    def initialize_schema(self) -> None:
        type(self).initialized = True

    def save(self, products: tuple[Product, ...]) -> int:
        type(self).saved_products = tuple(products)
        return len(products)

    def __enter__(self) -> CapturingStorage:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class PassthroughParser:
    def __init__(self, product: Product) -> None:
        self.product = product

    def parse(self, html: str, sku: str) -> Product:
        assert html == f"html:{sku}"
        return self.product


class BatchSettings:
    def __init__(self, results_dir: Path) -> None:
        self.ozon_skus = ("111", "222")
        self.results_dir = results_dir


def test_configured_batch_uses_skus_from_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_product: Product,
) -> None:
    CapturingStorage.saved_products = ()
    CapturingStorage.initialized = False
    client = ContextClient(
        {
            "111": "html:111",
            "222": "html:222",
        }
    )

    monkeypatch.setattr("ozon_client.create_ozon_client", lambda _: client)
    monkeypatch.setattr(
        "parse_ozon.ProductParser", lambda: PassthroughParser(sample_product)
    )
    monkeypatch.setattr(
        "parse_ozon.PostgresProductStorage", CapturingStorage
    )

    result = run_configured_batch(BatchSettings(tmp_path))

    assert result.products == (sample_product, sample_product)
    assert client.requested_skus == ["111", "222"]
    assert CapturingStorage.initialized is True
    assert CapturingStorage.saved_products == result.products
