"""The parsed HTML tree career pages are read from, and the lines a reader sees in it."""
from __future__ import annotations

import re
from html.parser import HTMLParser


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

_HIDDEN = {"script", "style", "svg", "noscript", "template"}

_DISPLAY_NONE = re.compile(r"(?:^|;)display:none(?:!important)?(?:;|$)")

_VISIBILITY_HIDDEN = re.compile(r"(?:^|;)visibility:hidden(?:!important)?(?:;|$)")

_DISPLAY_BLOCK = re.compile(r"(?:^|;)display:block(?:!important)?(?:;|$)")

def _element_hidden(tag: str, attrs: dict[str, str]) -> bool:
    style = re.sub(r"\s+", "", attrs.get("style", "")).casefold()
    classes = set(attrs.get("class", "").casefold().split())
    modal_hidden = ("modal" in classes and "show" not in classes and
                    attrs.get("aria-hidden", "").casefold() != "false" and
                    attrs.get("aria-modal", "").casefold() != "true" and
                    not _DISPLAY_BLOCK.search(style))
    return bool(
        tag in _HIDDEN or "hidden" in attrs or attrs.get("aria-hidden", "").casefold() == "true" or
        classes.intersection({"d-none", "hidden", "invisible"}) or modal_hidden or
        _DISPLAY_NONE.search(style) or _VISIBILITY_HIDDEN.search(style)
    )

class _Element:
    def __init__(self, tag: str, attrs: dict[str, str], parent: _Element | None = None):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.hidden = bool((parent and parent.hidden) or _element_hidden(tag, attrs))
        self.children: list[_Element | str] = []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Element):
                yield from child.walk()

    def text(self) -> str:
        if self.hidden:
            return ""
        values = []
        for child in self.children:
            if isinstance(child, _Element):
                value = child.text()
            else:
                value = child
            if value.strip():
                values.append(value)
        return _clean_text(" ".join(values))

class _TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Element(tag.casefold(), {key.casefold(): value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.casefold() not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)

def _clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[\u00ad\u200b-\u200f\u2060\ufeff]", "", value)
    return " ".join(value.split())

# Reading facts from what a page shows rather than from how it is marked up.
# Career sites put the town, hours and contract type in short lines near the
# title ("Standort" over "Mannheim, hybrid", "Vollzeit | Essen | Hybrid", a
# span beside a map icon). Those lines are found by layout and accepted by
# content: a place must be a real municipality, an employment type must be the
# whole fact, not a word inside a sentence.
# Search filters list roles as options ("Prophylaxe (w/m/d)"); they are controls, not postings.
_FORM_CONTROLS = frozenset({"select", "option", "optgroup", "textarea"})

_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "button", "dd", "div", "dl", "dt", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "label", "li", "main", "nav", "ol", "p", "section",
    "table", "td", "th", "tr", "ul",
})

def _lines(node: _Element, *, after: _Element | None = None) -> list[str]:
    """The visible text of ``node`` as the lines a reader sees, optionally after one element."""
    lines: list[list[str]] = [[]]
    active = after is None

    def visit(item):
        nonlocal active
        if isinstance(item, str):
            if active and item.strip():
                lines[-1].append(item.strip())
            return
        if item is after:
            active = True
            lines.append([])
            return
        if item.hidden or item.tag in _FORM_CONTROLS or _SCREEN_READER_ONLY.search(item.attrs.get("class", "")):
            return
        block = item.tag in _BLOCK_TAGS
        if block:
            lines.append([])
        for child in item.children:
            visit(child)
        if block:
            lines.append([])

    visit(node)
    return [_clean_text(" ".join(parts)) for parts in lines if parts and _clean_text(" ".join(parts))]

_SCREEN_READER_ONLY = re.compile(r"(?:^|\s)(?:sr-only|screen-reader-text|visually-hidden|[\w-]*-visually-hidden|visuallyhidden)(?:\s|$)")

def _visible_text(node: _Element) -> str:
    """Rendered text without screen-reader-only labels ("All Locations")."""
    if node.hidden or _SCREEN_READER_ONLY.search(node.attrs.get("class", "")):
        return ""
    values = []
    for child in node.children:
        value = _visible_text(child) if isinstance(child, _Element) else child
        if value.strip():
            values.append(value)
    return _clean_text(" ".join(values))

def _ancestors(node: _Element):
    current = node.parent
    while current is not None:
        yield current
        current = current.parent
