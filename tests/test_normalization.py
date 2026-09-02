"""Tests for pure product normalization helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from parsers.product_parser import (
    find_characteristic,
    has_rich_content,
    parse_price,
    parse_rating,
    parse_reviews_total,
)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("1 999 ₽", Decimal("1999")),
        ("1\u00a0999 ₽", Decimal("1999")),
        ("1999₽", Decimal("1999")),
        ("1\u202f999,50 ₽", Decimal("1999.50")),
        (1999, Decimal("1999")),
        (None, None),
        ("нет цены", None),
    ],
)
def test_parse_price(raw_value: object, expected: Decimal | None) -> None:
    assert parse_price(raw_value) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("4.8", 4.8),
        ("4,8 из 5", 4.8),
        (5, 5.0),
        (None, None),
        ("нет рейтинга", None),
    ],
)
def test_parse_rating(raw_value: object, expected: float | None) -> None:
    assert parse_rating(raw_value) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("1 отзыв", 1),
        ("15 отзывов", 15),
        ("1 234 отзыва", 1234),
        ("1\u202f234 отзыва", 1234),
        (1234, 1234),
        (None, None),
        ("нет отзывов", None),
    ],
)
def test_parse_reviews_total(raw_value: object, expected: int | None) -> None:
    assert parse_reviews_total(raw_value) == expected


def test_find_characteristic_accepts_alias_and_normalizes_value() -> None:
    characteristics = [
        {"name": " Название цвета: ", "value": "  Красный  "},
        {
            "name": "Материал",
            "value": ["Хлопок", "Полиэстер", "Хлопок"],
        },
    ]

    assert (
        find_characteristic(
            characteristics,
            ["Цвет", "Название цвета"],
        )
        == "Красный"
    )
    assert find_characteristic(characteristics, ["Материал"]) == (
        "Хлопок, Полиэстер"
    )


def test_find_characteristic_returns_none_for_missing_data() -> None:
    assert find_characteristic(None, ["Цвет"]) is None
    assert find_characteristic([], ["Цвет"]) is None
    assert find_characteristic([{"name": "Цвет"}], ["Цвет"]) is None


@pytest.mark.parametrize(
    "description",
    [
        '<p><img src="image.jpg"></p>',
        "<table><tr><td>Значение</td></tr></table>",
        "<ul><li>Пункт</li></ul>",
        "<ol><li>Пункт</li></ol>",
        '<video src="video.mp4"></video>',
    ],
)
def test_has_rich_content_detects_structural_html(description: str) -> None:
    assert has_rich_content(description) is True


def test_has_rich_content_does_not_use_text_length() -> None:
    assert has_rich_content("Очень длинный текст " * 500) is False
    assert has_rich_content("<p>Обычный текст</p>") is False
    assert has_rich_content(None) is False
