"""PostgreSQL persistence for the current product snapshot."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from models.product import Product
from utils.exceptions import StorageError

if TYPE_CHECKING:
    from config import Settings


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = PROJECT_DIR / "sql" / "schema.sql"

PRODUCT_COLUMNS = (
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
HISTORY_COLUMNS = (
    "sku",
    "price",
    "rating",
    "reviews_total",
    "parsed_at",
)

UPSERT_PRODUCT_SQL = """
INSERT INTO products (
    sku,
    title,
    price,
    rating,
    reviews_total,
    cover_image,
    photos_seller,
    videos_seller,
    color,
    material,
    art_set,
    has_rich_content,
    parsed_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s
)
ON CONFLICT (sku) DO UPDATE SET
    title = EXCLUDED.title,
    price = EXCLUDED.price,
    rating = EXCLUDED.rating,
    reviews_total = EXCLUDED.reviews_total,
    cover_image = EXCLUDED.cover_image,
    photos_seller = EXCLUDED.photos_seller,
    videos_seller = EXCLUDED.videos_seller,
    color = EXCLUDED.color,
    material = EXCLUDED.material,
    art_set = EXCLUDED.art_set,
    has_rich_content = EXCLUDED.has_rich_content,
    parsed_at = EXCLUDED.parsed_at
""".strip()

INSERT_HISTORY_SQL = """
INSERT INTO product_history (
    sku,
    price,
    rating,
    reviews_total,
    parsed_at
) VALUES (%s, %s, %s, %s, %s)
""".strip()


class Cursor(Protocol):
    def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> Any:
        ...

    def executemany(
        self,
        query: str,
        params_seq: Sequence[Sequence[Any]],
    ) -> Any:
        ...

    def __enter__(self) -> Cursor:
        ...

    def __exit__(self, *args: object) -> None:
        ...


class Connection(Protocol):
    def cursor(self) -> Cursor:
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...

    def close(self) -> None:
        ...


def product_to_db_params(product: Product) -> tuple[Any, ...]:
    """Map Product fields to the stable UPSERT parameter order."""
    return tuple(getattr(product, column) for column in PRODUCT_COLUMNS)


def product_to_history_params(product: Product) -> tuple[Any, ...]:
    """Map changing metrics to the history INSERT parameter order."""
    return tuple(getattr(product, column) for column in HISTORY_COLUMNS)


class PostgresProductStorage:
    """Persist current products and an append-only metrics history."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    @classmethod
    def from_settings(cls, settings: Settings) -> PostgresProductStorage:
        """Open a PostgreSQL connection without exposing credentials in logs."""
        try:
            import psycopg
        except ImportError as exc:
            raise StorageError(
                "psycopg is not installed; install project requirements"
            ) from exc

        try:
            connection = psycopg.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                dbname=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.require_postgres_password(),
                connect_timeout=10,
            )
        except Exception as exc:
            raise StorageError("Cannot connect to PostgreSQL") from exc
        return cls(connection)

    def __enter__(self) -> PostgresProductStorage:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def initialize_schema(self, path: Path = DEFAULT_SCHEMA_PATH) -> None:
        """Apply the idempotent current and history schema in one transaction."""
        try:
            schema_sql = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"Cannot read PostgreSQL schema: {path}") from exc

        if not schema_sql.strip():
            raise StorageError(f"PostgreSQL schema is empty: {path}")

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(schema_sql)
            self.connection.commit()
        except Exception as exc:
            self._rollback()
            raise StorageError("Cannot initialize PostgreSQL schema") from exc

    def save(self, products: Iterable[Product]) -> int:
        """UPSERT current rows and append history in one transaction."""
        product_list = list(products)
        if not product_list:
            return 0

        current_params = [
            product_to_db_params(product)
            for product in product_list
        ]
        history_params = [
            product_to_history_params(product)
            for product in product_list
        ]

        try:
            with self.connection.cursor() as cursor:
                cursor.executemany(UPSERT_PRODUCT_SQL, current_params)
                cursor.executemany(INSERT_HISTORY_SQL, history_params)
            self.connection.commit()
        except Exception as exc:
            self._rollback()
            raise StorageError(
                "Cannot save products and history to PostgreSQL"
            ) from exc
        return len(product_list)

    def _rollback(self) -> None:
        try:
            self.connection.rollback()
        except Exception:
            pass
