"""Safe extraction of JSON values embedded in Ozon product HTML."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal


JsonSource = Literal["script", "data-state"]


@dataclass(frozen=True, slots=True)
class EmbeddedJsonBlock:
    """One successfully decoded JSON value and its HTML origin."""

    source: JsonSource
    identifier: str | None
    mime_type: str | None
    data: Any


@dataclass(frozen=True, slots=True)
class EmbeddedJsonIssue:
    """A JSON candidate that was found but could not be decoded."""

    source: JsonSource
    identifier: str | None
    message: str


@dataclass(frozen=True, slots=True)
class EmbeddedJsonResult:
    """Extraction result that keeps valid blocks separate from safe errors."""

    blocks: tuple[EmbeddedJsonBlock, ...]
    issues: tuple[EmbeddedJsonIssue, ...]


def _normalize_mime_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.partition(";")[0].strip().casefold()
    return normalized or None


def _is_json_mime_type(value: str | None) -> bool:
    normalized = _normalize_mime_type(value)
    if normalized is None:
        return False
    return normalized in {"application/json", "application/ld+json"} or (
        normalized.startswith("application/") and normalized.endswith("+json")
    )


class _EmbeddedJsonHtmlParser(HTMLParser):
    """Collect only explicit JSON scripts and Ozon state containers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[EmbeddedJsonBlock] = []
        self.issues: list[EmbeddedJsonIssue] = []
        self._script_identifier: str | None = None
        self._script_mime_type: str | None = None
        self._script_chunks: list[str] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {name.casefold(): value for name, value in attrs}
        normalized_tag = tag.casefold()

        if normalized_tag == "script":
            mime_type = attributes.get("type")
            if _is_json_mime_type(mime_type):
                self._script_identifier = attributes.get("id")
                self._script_mime_type = _normalize_mime_type(mime_type)
                self._script_chunks = []
            return

        if normalized_tag != "div":
            return

        identifier = attributes.get("id")
        raw_state = attributes.get("data-state")
        if (
            isinstance(identifier, str)
            and identifier.startswith("state-")
            and raw_state is not None
        ):
            self._decode_and_append(
                raw_state,
                source="data-state",
                identifier=identifier,
                mime_type=None,
            )

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self._script_chunks is not None:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or self._script_chunks is None:
            return

        raw_json = "".join(self._script_chunks).strip()
        self._decode_and_append(
            raw_json,
            source="script",
            identifier=self._script_identifier,
            mime_type=self._script_mime_type,
        )
        self._script_identifier = None
        self._script_mime_type = None
        self._script_chunks = None

    def close(self) -> None:
        super().close()
        if self._script_chunks is not None:
            self.issues.append(
                EmbeddedJsonIssue(
                    source="script",
                    identifier=self._script_identifier,
                    message="JSON script is not closed",
                )
            )
            self._script_identifier = None
            self._script_mime_type = None
            self._script_chunks = None

    def _decode_and_append(
        self,
        raw_json: str,
        *,
        source: JsonSource,
        identifier: str | None,
        mime_type: str | None,
    ) -> None:
        if not raw_json:
            self.issues.append(
                EmbeddedJsonIssue(
                    source=source,
                    identifier=identifier,
                    message="JSON value is empty",
                )
            )
            return

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            self.issues.append(
                EmbeddedJsonIssue(
                    source=source,
                    identifier=identifier,
                    message=(
                        f"Invalid JSON at line {exc.lineno}, column {exc.colno}"
                    ),
                )
            )
            return

        self.blocks.append(
            EmbeddedJsonBlock(
                source=source,
                identifier=identifier,
                mime_type=mime_type,
                data=data,
            )
        )


def extract_embedded_json(html: str) -> EmbeddedJsonResult:
    """Decode explicit JSON blocks without interpreting arbitrary JavaScript."""
    parser = _EmbeddedJsonHtmlParser()
    parser.feed(html)
    parser.close()
    return EmbeddedJsonResult(
        blocks=tuple(parser.blocks),
        issues=tuple(parser.issues),
    )


def _is_product_type(value: Any) -> bool:
    if isinstance(value, str):
        return value.casefold() == "product"
    if isinstance(value, list):
        return any(
            isinstance(item, str) and item.casefold() == "product"
            for item in value
        )
    return False


def _iter_json_ld_nodes(data: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_json_ld_nodes(item)
        return

    if not isinstance(data, Mapping):
        return

    yield data
    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _iter_json_ld_nodes(item)


def find_json_ld_products(
    blocks: tuple[EmbeddedJsonBlock, ...] | list[EmbeddedJsonBlock],
) -> list[Mapping[str, Any]]:
    """Return schema.org Product nodes from decoded JSON-LD scripts."""
    products: list[Mapping[str, Any]] = []
    for block in blocks:
        if block.source != "script" or block.mime_type != "application/ld+json":
            continue
        for node in _iter_json_ld_nodes(block.data):
            if _is_product_type(node.get("@type")):
                products.append(node)
    return products


def find_json_ld_product(
    blocks: tuple[EmbeddedJsonBlock, ...] | list[EmbeddedJsonBlock],
    *,
    sku: str | None = None,
) -> Mapping[str, Any] | None:
    """Find a Product node, preferring an exact SKU when it is supplied."""
    products = find_json_ld_products(blocks)
    if sku is None:
        return products[0] if products else None

    normalized_sku = sku.strip()
    for product in products:
        product_sku = product.get("sku")
        if product_sku is not None and str(product_sku).strip() == normalized_sku:
            return product
    return None
