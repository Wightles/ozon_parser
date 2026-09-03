"""Daily Airflow pipeline for authenticated Ozon product parsing."""

from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path
from typing import Any

import pendulum
from airflow.sdk import dag, task
from airflow.sdk.exceptions import AirflowException

from config import get_settings
from ozon_client import load_cookies
from parse_ozon import run_configured_batch
from storage.postgres_storage import PostgresProductStorage


DAG_ID = "ozon_product_parser"
SCHEDULE = "0 6 * * *"


def _check_environment() -> dict[str, str]:
    settings = get_settings()
    load_cookies(settings.cookies_path)
    settings.require_postgres_password()
    with PostgresProductStorage.from_settings(settings) as storage:
        storage.check_connection()
    return {
        "cookies": "available",
        "database": settings.postgres_db,
    }


def _parse_products() -> dict[str, Any]:
    settings = get_settings()
    result = run_configured_batch(settings)
    successful_skus = [product.sku for product in result.products]
    if not successful_skus:
        raise AirflowException("Parser did not process any SKU successfully")
    return {
        "successful_skus": successful_skus,
        "failed_skus": list(result.failed_skus),
        "csv_path": str(settings.results_dir / "products.csv"),
    }


def _read_csv_skus(path: Path) -> set[str]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as file:
            return {
                row["sku"].strip()
                for row in csv.DictReader(file)
                if row.get("sku") and row["sku"].strip()
            }
    except (OSError, csv.Error, KeyError) as exc:
        raise AirflowException(f"Cannot validate parser CSV: {path}") from exc


def _validate_results(summary: dict[str, Any]) -> dict[str, Any]:
    successful_skus = summary.get("successful_skus")
    csv_path = summary.get("csv_path")
    if not isinstance(successful_skus, list) or not successful_skus:
        raise AirflowException("Parser result contains no successful SKU values")
    if not all(isinstance(sku, str) and sku for sku in successful_skus):
        raise AirflowException("Parser result contains invalid SKU values")
    if not isinstance(csv_path, str) or not csv_path:
        raise AirflowException("Parser result contains no CSV path")

    expected_skus = set(successful_skus)
    csv_skus = _read_csv_skus(Path(csv_path))
    missing_csv_skus = expected_skus - csv_skus
    if missing_csv_skus:
        raise AirflowException(
            f"CSV is missing {len(missing_csv_skus)} parsed SKU values"
        )

    settings = get_settings()
    with PostgresProductStorage.from_settings(settings) as storage:
        database_skus = storage.find_existing_skus(expected_skus)
    missing_database_skus = expected_skus - database_skus
    if missing_database_skus:
        raise AirflowException(
            "PostgreSQL is missing "
            f"{len(missing_database_skus)} parsed SKU values"
        )
    return summary


@dag(
    dag_id=DAG_ID,
    description="Daily authenticated parsing of configured Ozon products",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Novosibirsk"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "ozon-parser",
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["ozon", "products"],
)
def ozon_product_parser_pipeline() -> None:
    @task(task_id="check_environment")
    def check_environment_task() -> dict[str, str]:
        return _check_environment()

    @task(task_id="parse_products")
    def parse_products_task(_: dict[str, str]) -> dict[str, Any]:
        return _parse_products()

    @task(task_id="validate_results")
    def validate_results_task(summary: dict[str, Any]) -> dict[str, Any]:
        return _validate_results(summary)

    environment = check_environment_task()
    result = parse_products_task(environment)
    validate_results_task(result)


ozon_product_parser = ozon_product_parser_pipeline()
