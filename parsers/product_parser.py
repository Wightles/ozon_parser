"""Convert confirmed Ozon JSON-LD product data into the domain model."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from models.product import Product
from parsers.embedded_json import (
    EmbeddedJsonBlock,
    extract_embedded_json,
    find_json_ld_product,
)
from utils.exceptions import ProductParseError


_NUMBER_PATTERN = re.compile(r"[+-]?\d[\d\s\u00a0\u202f.,]*")
_RATING_PATTERN = re.compile(r"[+-]?\d+(?:[.,]\d+)?")
_REVIEWS_PATTERN = re.compile(r"\d[\d\s\u00a0\u202f]*")
_RICH_CONTENT_TAGS = frozenset(
    {"img", "picture", "video", "table", "ul", "ol", "li"}
)

COLOR_NAMES = ("Цвет", "Название цвета")
MATERIAL_NAMES = ("Материал", "Материал изделия")
ART_SET_NAMES = (
    "Артикул производителя",
    "Артикул",
    "Комплектация",
)

Characteristic = Mapping[str, Any] | tuple[str, Any]


def _finite_decimal(value: Decimal) -> Decimal | None:
    return value if value.is_finite() else None


def _normalize_decimal_text(raw_value: str) -> str | None:
    match = _NUMBER_PATTERN.search(raw_value)
    if match is None:
        return None

    number = re.sub(r"[\s\u00a0\u202f]", "", match.group(0))
    comma_position = number.rfind(",")
    dot_position = number.rfind(".")

    if comma_position >= 0 and dot_position >= 0:
        decimal_separator = "," if comma_position > dot_position else "."
        thousands_separator = "." if decimal_separator == "," else ","
        number = number.replace(thousands_separator, "")
        if decimal_separator == ",":
            number = number.replace(",", ".")
    elif comma_position >= 0:
        number = number.replace(",", ".")

    if number.count(".") > 1:
        parts = number.split(".")
        if all(len(part) == 3 for part in parts[1:]):
            number = "".join(parts)
        else:
            return None
    return number


def parse_price(value: Any) -> Decimal | None:
    """Normalize a numeric price or a formatted Russian price string."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return _finite_decimal(value)
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))
    if not isinstance(value, str):
        return None

    normalized = _normalize_decimal_text(value)
    if normalized is None:
        return None
    try:
        return _finite_decimal(Decimal(normalized))
    except InvalidOperation:
        return None


def parse_rating(value: Any) -> float | None:
    """Normalize the first decimal rating found in a scalar value."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            rating = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return rating if math.isfinite(rating) else None
    if not isinstance(value, str):
        return None

    match = _RATING_PATTERN.search(value)
    if match is None:
        return None
    try:
        rating = float(match.group(0).replace(",", "."))
    except ValueError:
        return None
    return rating if math.isfinite(rating) else None


def parse_reviews_total(value: Any) -> int | None:
    """Normalize an integer review count with optional grouped spaces."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        try:
            integer = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return integer if value == integer else None
    if not isinstance(value, str):
        return None

    match = _REVIEWS_PATTERN.search(value)
    if match is None:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits) if digits else None


def _normalize_characteristic_name(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized.rstrip(":").strip().casefold()


def _characteristic_parts(item: Characteristic) -> tuple[Any, Any]:
    if isinstance(item, Mapping):
        return item.get("name"), item.get("value")
    if isinstance(item, tuple) and len(item) == 2:
        return item
    return None, None


def _characteristic_value(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = " ".join(value.split())
        return normalized or None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        parts = [_characteristic_value(item) for item in value]
        unique_parts = list(dict.fromkeys(part for part in parts if part))
        return ", ".join(unique_parts) or None
    return None


def find_characteristic(
    characteristics: Iterable[Characteristic] | None,
    names: Iterable[str],
) -> str | None:
    """Find a value by any normalized characteristic name."""
    expected_names = {
        _normalize_characteristic_name(name)
        for name in names
        if isinstance(name, str) and name.strip()
    }
    if not expected_names or characteristics is None:
        return None

    for item in characteristics:
        name, value = _characteristic_parts(item)
        if not isinstance(name, str):
            continue
        if _normalize_characteristic_name(name) not in expected_names:
            continue
        normalized_value = _characteristic_value(value)
        if normalized_value is not None:
            return normalized_value
    return None


class _RichContentHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if tag.casefold() in _RICH_CONTENT_TAGS:
            self.found = True

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)


def has_rich_content(description: Any) -> bool:
    """Detect structural rich HTML without relying on description length."""
    if not isinstance(description, str) or "<" not in description:
        return False
    parser = _RichContentHtmlParser()
    parser.feed(description)
    parser.close()
    return parser.found


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _first_offer(value: Any) -> Mapping[str, Any] | None:
    offer = _mapping(value)
    if offer is not None:
        return offer
    if isinstance(value, list):
        return next(
            (item for item in value if isinstance(item, Mapping)),
            None,
        )
    return None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _image_urls(value: Any) -> list[str]:
    candidates = value if isinstance(value, list) else [value]
    urls: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        url = candidate.strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def _find_data_state(
    blocks: Iterable[EmbeddedJsonBlock], identifier_prefix: str
) -> Mapping[str, Any] | None:
    for block in blocks:
        if (
            block.source == "data-state"
            and (block.identifier or "").startswith(identifier_prefix)
            and isinstance(block.data, Mapping)
        ):
            return block.data
    return None


def _gallery_image_urls(gallery: Mapping[str, Any] | None) -> list[str]:
    if gallery is None or not isinstance(gallery.get("images"), list):
        return []
    return _image_urls(
        [
            item.get("src")
            for item in gallery["images"]
            if isinstance(item, Mapping)
        ]
    )


def _gallery_video_count(gallery: Mapping[str, Any] | None) -> int:
    if gallery is None or not isinstance(gallery.get("videos"), list):
        return 0
    urls = _image_urls(
        [
            item.get("url")
            for item in gallery["videos"]
            if isinstance(item, Mapping)
        ]
    )
    return len(urls)


def _short_characteristics(
    state: Mapping[str, Any] | None,
) -> list[Characteristic]:
    if state is None or not isinstance(state.get("characteristics"), list):
        return []

    result: list[Characteristic] = []
    for item in state["characteristics"]:
        if not isinstance(item, Mapping):
            continue
        title = item.get("title")
        text_runs = title.get("textRs") if isinstance(title, Mapping) else None
        if not isinstance(text_runs, list):
            continue
        name = "".join(
            str(run.get("content", ""))
            for run in text_runs
            if isinstance(run, Mapping)
        ).strip()
        values = item.get("values")
        if not name or not isinstance(values, list):
            continue
        value_parts = [
            str(value.get("text", "")).strip(" ,")
            for value in values
            if isinstance(value, Mapping)
            and str(value.get("text", "")).strip(" ,")
        ]
        if value_parts:
            result.append((name, value_parts))
    return result


class ProductParser:
    """Parse the confirmed schema.org Product node from an Ozon HTML page."""

    def parse(
        self,
        html: str,
        sku: str,
        *,
        characteristics: Iterable[Characteristic] | None = None,
        parsed_at: datetime | None = None,
    ) -> Product:
        if not isinstance(sku, str):
            raise ProductParseError("Ozon SKU must contain digits only")
        normalized_sku = sku.strip()
        if not normalized_sku.isdigit():
            raise ProductParseError("Ozon SKU must contain digits only")
        if not isinstance(html, str) or not html.strip():
            raise ProductParseError(
                f"Cannot parse an empty HTML page for SKU {normalized_sku}"
            )

        extraction = extract_embedded_json(html)
        product_data = find_json_ld_product(
            extraction.blocks,
            sku=normalized_sku,
        )
        if product_data is None:
            issue_suffix = (
                f"; invalid JSON blocks: {len(extraction.issues)}"
                if extraction.issues
                else ""
            )
            raise ProductParseError(
                f"JSON-LD Product was not found for SKU {normalized_sku}"
                f"{issue_suffix}"
            )

        offer = _first_offer(product_data.get("offers"))
        aggregate_rating = _mapping(product_data.get("aggregateRating"))
        json_ld_images = _image_urls(product_data.get("image"))
        description = product_data.get("description")
        gallery = _find_data_state(extraction.blocks, "state-webGallery-")
        gallery_images = _gallery_image_urls(gallery)
        images = gallery_images or json_ld_images
        state_characteristics = _short_characteristics(
            _find_data_state(
                extraction.blocks,
                "state-webShortCharacteristics-",
            )
        )
        available_characteristics = (
            characteristics
            if characteristics is not None
            else state_characteristics
        )
        gallery_cover = (
            _optional_text(gallery.get("coverImage")) if gallery else None
        )

        return Product(
            sku=normalized_sku,
            title=_optional_text(product_data.get("name")),
            price=parse_price(offer.get("price")) if offer else None,
            rating=(
                parse_rating(aggregate_rating.get("ratingValue"))
                if aggregate_rating
                else None
            ),
            reviews_total=(
                parse_reviews_total(aggregate_rating.get("reviewCount"))
                if aggregate_rating
                else None
            ),
            cover_image=gallery_cover or (images[0] if images else None),
            photos_seller=len(images),
            videos_seller=_gallery_video_count(gallery),
            color=find_characteristic(available_characteristics, COLOR_NAMES),
            material=find_characteristic(
                available_characteristics,
                MATERIAL_NAMES,
            ),
            art_set=find_characteristic(
                available_characteristics,
                ART_SET_NAMES,
            ),
            has_rich_content=has_rich_content(description),
            parsed_at=parsed_at or datetime.now(timezone.utc),
        )


def parse_product(html: str, sku: str) -> Product:
    """Convenience wrapper for parsing one product page."""
    return ProductParser().parse(html, sku)
