"""Shared deterministic pytest fixtures."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from models.product import Product


@pytest.fixture
def parsed_at() -> datetime:
    return datetime(2026, 9, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def sample_product(parsed_at: datetime) -> Product:
    return Product(
        sku="2359066702",
        title="Тестовый товар",
        price=Decimal("1999.50"),
        rating=4.8,
        reviews_total=1234,
        cover_image="https://cdn.example/cover.jpg",
        photos_seller=2,
        videos_seller=0,
        color="Красный",
        material=None,
        art_set="ABC-123",
        has_rich_content=True,
        parsed_at=parsed_at,
    )


@pytest.fixture
def product_json_ld_html() -> str:
    product_data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "sku": "2359066702",
        "name": "Тестовый товар",
        "offers": {"@type": "Offer", "price": "1 999,50 ₽"},
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": "4,8",
            "reviewCount": "1 234 отзыва",
        },
        "image": [
            "https://cdn.example/cover.jpg",
            "https://cdn.example/gallery.jpg",
            "https://cdn.example/cover.jpg",
        ],
        "description": "<p>Описание</p><ul><li>Пункт</li></ul>",
    }
    encoded_product = json.dumps(product_data, ensure_ascii=False)
    return (
        "<html><head>"
        f'<script type="application/ld+json">{encoded_product}</script>'
        "</head><body></body></html>"
    )


@pytest.fixture
def valid_cookies_path(tmp_path: Path) -> Path:
    path = tmp_path / "cookies.json"
    cookies = [
        {
            "name": "session_id",
            "value": "test-cookie-value",
            "domain": ".ozon.ru",
            "path": "/",
            "expires": 2_000_000_000,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
    ]
    path.write_text(json.dumps(cookies), encoding="utf-8")
    return path
