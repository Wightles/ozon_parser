"""Product persistence implementations."""

from storage.csv_storage import (
    CSV_FIELDS,
    CsvProductStorage,
    product_to_csv_row,
    save_products_csv,
)
from storage.postgres_storage import (
    PRODUCT_COLUMNS,
    UPSERT_PRODUCT_SQL,
    PostgresProductStorage,
    product_to_db_params,
)

__all__ = [
    "CSV_FIELDS",
    "CsvProductStorage",
    "product_to_csv_row",
    "save_products_csv",
    "PRODUCT_COLUMNS",
    "UPSERT_PRODUCT_SQL",
    "PostgresProductStorage",
    "product_to_db_params",
]
