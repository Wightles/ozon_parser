"""Tests for per-SKU failure isolation in the batch parser."""

from __future__ import annotations

from pathlib import Path

import pytest

import parse_ozon
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
        self.log_level = "INFO"


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


def test_configured_batch_can_skip_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_product: Product,
) -> None:
    CapturingStorage.saved_products = ()
    CapturingStorage.initialized = False
    client = ContextClient({"111": "html:111"})

    def fail_if_database_is_used(_settings: object) -> object:
        raise AssertionError("database should be skipped")

    monkeypatch.setattr("ozon_client.create_ozon_client", lambda _: client)
    monkeypatch.setattr(
        "parse_ozon.ProductParser", lambda: PassthroughParser(sample_product)
    )
    monkeypatch.setattr(
        "parse_ozon.PostgresProductStorage.from_settings",
        fail_if_database_is_used,
    )

    result = run_configured_batch(
        BatchSettings(tmp_path),
        skus=("111",),
        save_database=False,
    )

    assert result.products == (sample_product,)
    assert client.requested_skus == ["111"]
    assert CapturingStorage.initialized is False
    assert CapturingStorage.saved_products == ()
    assert (tmp_path / "products.csv").is_file()


def test_configured_batch_can_write_custom_output_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_product: Product,
) -> None:
    client = ContextClient({"111": "html:111"})
    output_path = tmp_path / "custom" / "ozon.csv"

    monkeypatch.setattr("ozon_client.create_ozon_client", lambda _: client)
    monkeypatch.setattr(
        "parse_ozon.ProductParser", lambda: PassthroughParser(sample_product)
    )
    monkeypatch.setattr(
        "parse_ozon.PostgresProductStorage.from_settings",
        lambda _settings: CapturingStorage(),
    )

    result = run_configured_batch(
        BatchSettings(tmp_path),
        skus=("111",),
        save_database=False,
        output_path=output_path,
    )

    assert result.products == (sample_product,)
    assert output_path.is_file()
    assert not (tmp_path / "products.csv").exists()


def test_parse_main_accepts_one_off_sku_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_product: Product,
) -> None:
    captured: dict[str, tuple[str, ...] | None] = {}

    def fake_run_configured_batch(
        settings: object,
        *,
        skus: tuple[str, ...] | None = None,
        save_database: bool = True,
        output_path: Path | None = None,
    ) -> object:
        captured["skus"] = skus
        captured["save_database"] = save_database
        captured["output_path"] = output_path
        return type("Result", (), {"has_failures": False})()

    monkeypatch.setattr(
        parse_ozon,
        "run_configured_batch",
        fake_run_configured_batch,
    )
    monkeypatch.setattr(
        "config.get_settings",
        lambda: BatchSettings(tmp_path),
    )

    result = parse_ozon.main(
        ["--sku", "2359066702,2829800382", "--sku", "123456789"]
    )

    assert result == 0
    assert captured["skus"] == (
        "2359066702",
        "2829800382",
        "123456789",
    )
    assert captured["save_database"] is True
    assert captured["output_path"] is None


def test_parse_main_accepts_csv_only_mode_and_output_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_configured_batch(
        settings: object,
        *,
        skus: tuple[str, ...] | None = None,
        save_database: bool = True,
        output_path: Path | None = None,
    ) -> object:
        del settings, skus
        captured["save_database"] = save_database
        captured["output_path"] = output_path
        return type("Result", (), {"has_failures": False})()

    monkeypatch.setattr(
        parse_ozon,
        "run_configured_batch",
        fake_run_configured_batch,
    )
    monkeypatch.setattr(
        "config.get_settings",
        lambda: BatchSettings(tmp_path),
    )

    assert parse_ozon.main(
        ["--csv-only", "--output", str(tmp_path / "check.csv")]
    ) == 0
    assert captured["save_database"] is False
    assert captured["output_path"] == tmp_path / "check.csv"


def test_parse_main_rejects_invalid_sku_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "config.get_settings",
        lambda: BatchSettings(tmp_path),
    )

    assert parse_ozon.main(["--sku", "not-a-sku"]) == 2
