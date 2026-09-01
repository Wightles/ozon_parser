"""Product domain model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Product:
    """A normalized snapshot of an Ozon product card."""

    sku: str
    title: str | None
    price: Decimal | None
    rating: float | None
    reviews_total: int | None
    cover_image: str | None
    photos_seller: int
    videos_seller: int
    color: str | None
    material: str | None
    art_set: str | None
    has_rich_content: bool
    parsed_at: datetime

