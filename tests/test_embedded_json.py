"""Tests for safe JSON extraction from HTML."""

from __future__ import annotations

from parsers.embedded_json import (
    extract_embedded_json,
    find_json_ld_product,
)


def test_extracts_supported_blocks_and_ignores_javascript(
    product_json_ld_html: str,
) -> None:
    html = product_json_ld_html.replace(
        "</body>",
        """
        <div id="state-webPrice-test"
             data-state='{"price":"1 999 ₽"}'></div>
        <script>window.fake = {"not": "json block"};</script>
        </body>
        """,
    )

    result = extract_embedded_json(html)

    assert len(result.blocks) == 2
    assert result.issues == ()
    assert result.blocks[0].mime_type == "application/ld+json"
    assert result.blocks[1].identifier == "state-webPrice-test"
    assert result.blocks[1].data == {"price": "1 999 ₽"}


def test_reports_invalid_json_without_losing_valid_blocks() -> None:
    html = """
    <script type="application/json" id="broken">{bad}</script>
    <script type="application/json" id="valid">{"ok": true}</script>
    """

    result = extract_embedded_json(html)

    assert len(result.blocks) == 1
    assert result.blocks[0].data == {"ok": True}
    assert len(result.issues) == 1
    assert result.issues[0].identifier == "broken"
    assert "Invalid JSON" in result.issues[0].message


def test_finds_product_inside_json_ld_graph() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@graph": [
        {"@type": "BreadcrumbList"},
        {"@type": ["Thing", "Product"], "sku": "2359066702"}
      ]
    }
    </script>
    """

    result = extract_embedded_json(html)
    product = find_json_ld_product(result.blocks, sku="2359066702")

    assert product is not None
    assert product["sku"] == "2359066702"
