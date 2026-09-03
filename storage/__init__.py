"""Product persistence implementations."""

from storage.csv_storage import (
    CSV_FIELDS,
    CsvProductStorage,
    product_to_csv_row,
    save_products_csv,
)
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

__all__ = [
    "CSV_FIELDS",
    "CsvProductStorage",
    "product_to_csv_row",
    "save_products_csv",
    "CHECK_CONNECTION_SQL",
    "FIND_PRODUCTS_SQL",
    "HISTORY_COLUMNS",
    "INSERT_HISTORY_SQL",
    "PRODUCT_COLUMNS",
    "UPSERT_PRODUCT_SQL",
    "PostgresProductStorage",
    "product_to_db_params",
    "product_to_history_params",
]
