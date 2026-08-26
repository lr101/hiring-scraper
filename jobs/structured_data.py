from __future__ import annotations

import json
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import Any


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_json_ld = False
        self._parts: list[str] = []
        self.documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        attributes = {key.casefold(): (value or "").casefold() for key, value in attrs}
        script_type = attributes.get("type", "").split(";", 1)[0].strip()
        if script_type == "application/ld+json":
            self._in_json_ld = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._in_json_ld:
            self.documents.append("".join(self._parts))
            self._in_json_ld = False


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []
        self._in_anchor = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        self._href = attributes.get("href", "")
        self._text = []
        self._in_anchor = True

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._in_anchor:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._in_anchor = False


def iter_json_ld_objects(html: str) -> Iterable[dict[str, Any]]:
    parser = _JsonLdParser()
    parser.feed(html)
    for document in parser.documents:
        try:
            parsed = json.loads(document)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        yield from walk_json_ld(parsed)


def walk_json_ld(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, dict | list):
                yield from walk_json_ld(child)
    elif isinstance(value, list):
        for item in value:
            yield from walk_json_ld(item)


def extract_links(html: str) -> list[tuple[str, str]]:
    parser = _LinkParser()
    parser.feed(html)
    return parser.links
