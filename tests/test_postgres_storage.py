"""Tests for PostgreSQL mapping, schema setup, and UPSERT behavior."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from models.product import Product
from storage.postgres_storage import (
    CHECK_CONNECTION_SQL,
    FIND_PRODUCTS_SQL,
    HISTORY_COLUMNS,
    INSERT_HISTORY_SQL,
    PRODUCT_COLUMNS,
    UPSERT_PRODUCT_SQL,
    PostgresProductStorage,
    product_to_db_params,
    product_to_history_params,
)
from utils.exceptions import StorageError


class FakeCursor:
    def __init__(
        self,
        *,
        fail: bool = False,
        fail_on_many_call: int | None = None,
        rows: Sequence[Sequence[Any]] = (),
    ) -> None:
        self.fail = fail
        self.fail_on_many_call = fail_on_many_call
        self.many_attempts = 0
        self.rows = rows
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
        self.many_attempts += 1
        if self.fail or self.many_attempts == self.fail_on_many_call:
            raise RuntimeError("synthetic database failure")
        self.executed_many.append((query, params_seq))

    def fetchall(self) -> Sequence[Sequence[Any]]:
        return self.rows

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeConnection:
    def __init__(
        self,
        *,
        fail: bool = False,
        fail_on_many_call: int | None = None,
        rows: Sequence[Sequence[Any]] = (),
    ) -> None:
        self.cursor_instance = FakeCursor(
            fail=fail,
            fail_on_many_call=fail_on_many_call,
            rows=rows,
        )
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


def test_save_updates_current_row_and_appends_history(
    sample_product: Product,
) -> None:
    connection = FakeConnection()
    storage = PostgresProductStorage(connection)

    row_count = storage.save([sample_product])

    assert row_count == 1
    assert connection.commits == 1
    assert connection.rollbacks == 0
    current_call, history_call = connection.cursor_instance.executed_many
    current_query, current_params = current_call
    history_query, history_params = history_call
    assert current_query == UPSERT_PRODUCT_SQL
    assert "ON CONFLICT (sku) DO UPDATE" in current_query
    assert current_params == [product_to_db_params(sample_product)]
    assert history_query == INSERT_HISTORY_SQL
    assert "ON CONFLICT" not in history_query
    assert history_params == [product_to_history_params(sample_product)]


def test_empty_save_does_not_open_transaction() -> None:
    connection = FakeConnection()

    row_count = PostgresProductStorage(connection).save([])

    assert row_count == 0
    assert connection.commits == 0
    assert connection.cursor_instance.executed_many == []


def test_initialize_schema_executes_tables_and_datalens_views() -> None:
    connection = FakeConnection()

    PostgresProductStorage(connection).initialize_schema()

    assert connection.commits == 1
    query, params = connection.cursor_instance.executed[0]
    assert "CREATE TABLE IF NOT EXISTS products" in query
    assert "CREATE TABLE IF NOT EXISTS product_history" in query
    assert "idx_product_history_sku_parsed_at" in query
    assert "CREATE OR REPLACE VIEW datalens_products" in query
    assert "CREATE OR REPLACE VIEW datalens_product_history" in query
    assert params is None


def test_database_error_rolls_back(sample_product: Product) -> None:
    connection = FakeConnection(fail_on_many_call=2)

    with pytest.raises(StorageError, match="Cannot save products and history"):
        PostgresProductStorage(connection).save([sample_product])

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert len(connection.cursor_instance.executed_many) == 1


def test_history_mapping_contains_only_changing_metrics(
    sample_product: Product,
) -> None:
    params = product_to_history_params(sample_product)

    assert len(params) == len(HISTORY_COLUMNS) == 5
    assert dict(zip(HISTORY_COLUMNS, params, strict=True)) == {
        "sku": sample_product.sku,
        "price": sample_product.price,
        "rating": sample_product.rating,
        "reviews_total": sample_product.reviews_total,
        "parsed_at": sample_product.parsed_at,
    }


def test_repeated_save_keeps_appending_history(
    sample_product: Product,
) -> None:
    connection = FakeConnection()
    storage = PostgresProductStorage(connection)

    storage.save([sample_product])
    storage.save([sample_product])

    calls = connection.cursor_instance.executed_many
    assert [query for query, _ in calls] == [
        UPSERT_PRODUCT_SQL,
        INSERT_HISTORY_SQL,
        UPSERT_PRODUCT_SQL,
        INSERT_HISTORY_SQL,
    ]
    assert connection.commits == 2


def test_check_connection_executes_minimal_query() -> None:
    connection = FakeConnection()

    PostgresProductStorage(connection).check_connection()

    assert connection.cursor_instance.executed == [
        (CHECK_CONNECTION_SQL, None)
    ]
    assert connection.commits == 1


def test_find_existing_skus_uses_parameterized_query() -> None:
    connection = FakeConnection(rows=[("2359066702",)])

    existing = PostgresProductStorage(connection).find_existing_skus(
        ["2359066702", "2829800382", "2359066702"]
    )

    assert existing == {"2359066702"}
    assert connection.cursor_instance.executed == [
        (
            FIND_PRODUCTS_SQL,
            (["2359066702", "2829800382"],),
        )
    ]
    assert connection.commits == 1
