"""Tests for PostgreSQL mapping, schema setup, and UPSERT behavior."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from models.product import Product
from storage.postgres_storage import (
    PRODUCT_COLUMNS,
    UPSERT_PRODUCT_SQL,
    PostgresProductStorage,
    product_to_db_params,
)
from utils.exceptions import StorageError


class FakeCursor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.executed: list[tuple[str, Sequence[Any] | None]] = []
        self.executed_many: list[
            tuple[str, Sequence[Sequence[Any]]]
        ] = []

    def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> None:
        if self.fail:
            raise RuntimeError("synthetic database failure")
        self.executed.append((query, params))

    def executemany(
        self,
        query: str,
        params_seq: Sequence[Sequence[Any]],
    ) -> None:
        if self.fail:
            raise RuntimeError("synthetic database failure")
        self.executed_many.append((query, params_seq))

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeConnection:
    def __init__(self, *, fail: bool = False) -> None:
        self.cursor_instance = FakeCursor(fail=fail)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_product_mapping_follows_column_order(sample_product: Product) -> None:
    params = product_to_db_params(sample_product)

    assert len(params) == len(PRODUCT_COLUMNS)
    assert dict(zip(PRODUCT_COLUMNS, params, strict=True)) == {
        column: getattr(sample_product, column)
        for column in PRODUCT_COLUMNS
    }


def test_save_uses_upsert_and_commits(sample_product: Product) -> None:
    connection = FakeConnection()
    storage = PostgresProductStorage(connection)

    row_count = storage.save([sample_product])

    assert row_count == 1
    assert connection.commits == 1
    assert connection.rollbacks == 0
    query, params_seq = connection.cursor_instance.executed_many[0]
    assert query == UPSERT_PRODUCT_SQL
    assert "ON CONFLICT (sku) DO UPDATE" in query
    assert params_seq == [product_to_db_params(sample_product)]


def test_empty_save_does_not_open_transaction() -> None:
    connection = FakeConnection()

    row_count = PostgresProductStorage(connection).save([])

    assert row_count == 0
    assert connection.commits == 0
    assert connection.cursor_instance.executed_many == []


def test_initialize_schema_executes_products_table(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text(
        "CREATE TABLE IF NOT EXISTS products (sku VARCHAR PRIMARY KEY);",
        encoding="utf-8",
    )
    connection = FakeConnection()

    PostgresProductStorage(connection).initialize_schema(schema_path)

    assert connection.commits == 1
    query, params = connection.cursor_instance.executed[0]
    assert "CREATE TABLE IF NOT EXISTS products" in query
    assert params is None


def test_database_error_rolls_back(sample_product: Product) -> None:
    connection = FakeConnection(fail=True)

    with pytest.raises(StorageError, match="Cannot UPSERT products"):
        PostgresProductStorage(connection).save([sample_product])

    assert connection.commits == 0
    assert connection.rollbacks == 1
