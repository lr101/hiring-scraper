"""Readable job description text, shared by every source adapter and the job page.

Sources deliver descriptions as HTML, as plain text with line breaks, or as text
that an upstream feed already flattened. Stored descriptions keep the source's
own structure as plain text: one heading per line, ``- `` bullets and a blank
line between blocks. That keeps them searchable and quotable for matching
(``requirements.structured_text`` reads the same shape) and lets the job page
render sections and lists without trusting source HTML.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

MAX_DESCRIPTION_CHARS = 24000

_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "header", "footer", "aside", "table", "tr",
    "blockquote", "pre", "dl", "dt", "dd", "figure", "ul", "ol",
}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "title", "button", "form"}
_BULLET = re.compile(r"^\s*(?:[-*•·▪◦‣–—]|\d{1,2}[.)])\s+")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_TRACKING_TAG = re.compile(r"(?:(?:## |- )?#?LI-[A-Za-z0-9]{1,12}\s*)+", re.I)


class _TextBlocks(HTMLParser):
    """Linearize HTML into heading, paragraph and bullet lines."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.current: list[str] = []
        self.kind = "p"
        self.skip = 0
        self.lists = 0
        self.strong = 0
        self.strong_only = True

    def _flush(self, *, gap: bool = True):
        text = " ".join("".join(self.current).split())
        if text:
            if self.kind == "h" or (self.kind == "p" and self.strong_only and _short_label(text)):
                self.lines.extend(["", "## " + text.strip(" :*_#"), ""])
            elif self.kind == "li":
                self.lines.append("- " + _BULLET.sub("", text))
            else:
                self.lines.extend(["", text, ""] if gap else [text])
        self.current, self.kind, self.strong_only = [], "p", True

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in _HEADING_TAGS:
            self._flush()
            self.kind = "h"
        elif tag == "li":
            self._flush()
            self.kind = "li"
        elif tag in {"ul", "ol"}:
            self._flush()
            self.lists += 1
        elif tag in _BLOCK_TAGS:
            if self.kind != "li":
                self._flush()
        elif tag == "br":
            if self.kind == "li":
                self.current.append(" ")
            else:
                self._flush(gap=False)
        elif tag in {"strong", "b"}:
            self.strong += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "hr", "img", "input"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in _HEADING_TAGS or tag == "li":
            self._flush()
        elif tag in {"ul", "ol"}:
            self._flush()
            self.lists = max(0, self.lists - 1)
            self.lines.append("")
        elif tag in _BLOCK_TAGS and self.kind != "li":
            self._flush()
        elif tag in {"strong", "b"}:
            self.strong = max(0, self.strong - 1)

    def handle_data(self, data):
        if self.skip:
            return
        if data.strip() and not self.strong:
            self.strong_only = False
        self.current.append(data)

    def text(self) -> str:
        self._flush()
        return "\n".join(self.lines)


def _short_label(text: str) -> bool:
    """A bold-only paragraph that reads like a section label ("Your profile:")."""
    words = text.split()
    return 0 < len(words) <= 8 and len(text) <= 70 and not re.search(r"[.!?]$", text)


def _tidy(text: str) -> str:
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t  -​  　]+", " ", line).strip()
        line = re.sub(r"[­​-‏⁠﻿]", "", line)
        lines.append(line)
    # Recruiter tracking tags ("#LI-JT1", "#LI-Remote") are not posting text.
    lines = [line for line in lines if not _TRACKING_TAG.fullmatch(line)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    # A heading directly followed by its list or paragraph needs no blank line.
    return re.sub(r"^(## [^\n]+)\n\n", r"\1\n", text, flags=re.M)


def description_text(value) -> str | None:
    """Source HTML or text as structured plain text, bounded in size."""
    if not isinstance(value, str) or not value.strip():
        return None
    source = value
    if not _TAG.search(source) and re.search(r"&lt;\s*/?\s*(?:p|br|li|ul|div|h[1-6]|strong)\b", source, re.I):
        source = html.unescape(source)  # HTML escaped once more by an XML or JSON feed.
    if _TAG.search(source):
        parser = _TextBlocks()
        parser.feed(source)
        parser.close()
        text = parser.text()
    else:
        text = html.unescape(source)
    text = _tidy(text)
    return text[:MAX_DESCRIPTION_CHARS].rstrip() or None


def description_preview(value, limit: int = 280) -> str:
    """One-line teaser without heading or bullet markers."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"^(?:## |- )", "", value, flags=re.M)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


# Display blocks -------------------------------------------------------------

_INLINE_BULLET = re.compile(r"\s+[•▪◦‣]\s+")


def _heading_line(line: str) -> str | None:
    if line.startswith("## "):
        return line[3:].strip(" :*_#") or None
    stripped = line.strip("*#_ ").strip()
    if not stripped or len(stripped) > 70 or _BULLET.match(line):
        return None
    from hiring_scraper.requirements import _OPTIONAL_HEADING, _OTHER_HEADING, _REQUIRED_HEADING
    label = stripped.rstrip(":*_ ").strip()
    known = (_REQUIRED_HEADING.fullmatch(label) or _OTHER_HEADING.fullmatch(label) or
             _OPTIONAL_HEADING.fullmatch(label))
    words = label.split()
    if known or (stripped.endswith(":") and len(words) <= 8) or (
            line.startswith(("**", "#")) and len(words) <= 10):
        return label
    return None


def _heading_section(heading: str) -> str:
    """duties, requirements, optional, benefits or neutral, as the matcher reads it."""
    from hiring_scraper.requirements import scoped_sentences
    scopes = [scope for _, scope in scoped_sentences(heading)]
    scope = scopes[0] if scopes else "neutral"
    return scope if scope in {"duties", "requirements", "optional", "benefits"} else "neutral"


def _recover_flat_structure(text: str) -> str:
    """Older rows and some feeds stored the whole posting on one line."""
    if "\n" in text.strip() or len(text) < 400:
        return text
    from hiring_scraper.requirements import _FLAT_SECTION_HEADING, _recovered_heading
    text = _FLAT_SECTION_HEADING.sub(lambda hit: _recovered_heading(hit, text), text)
    text = _INLINE_BULLET.sub("\n- ", text)
    if text.lstrip().startswith(("•", "▪", "◦", "‣")):
        text = "- " + text.lstrip()[1:].lstrip()
    return text


def description_blocks(value) -> list[dict]:
    """Headings, paragraphs and lists for the job page, in source order."""
    text = description_text(value)
    if not text:
        return []
    text = _recover_flat_structure(text)
    blocks: list[dict] = []
    paragraph: list[str] = []

    def end_paragraph():
        if paragraph:
            blocks.append({"type": "paragraph", "text": "\n".join(paragraph)})
            paragraph.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            end_paragraph()
            continue
        heading = _heading_line(line)
        if heading:
            end_paragraph()
            blocks.append({"type": "heading", "text": heading, "section": _heading_section(heading)})
            continue
        if _BULLET.match(line):
            end_paragraph()
            item = _BULLET.sub("", line).strip()
            if blocks and blocks[-1]["type"] == "list":
                blocks[-1]["items"].append(item)
            else:
                blocks.append({"type": "list", "items": [item]})
            continue
        if blocks and blocks[-1]["type"] == "list" and not paragraph and raw.startswith((" ", "\t")):
            blocks[-1]["items"][-1] += " " + line
            continue
        paragraph.append(line)
    end_paragraph()
    for block in blocks:
        if block["type"] == "paragraph" and len(block["text"]) > 900 and "\n" not in block["text"]:
            # Very long single paragraphs are flattened sources; split on sentences.
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", block["text"])
            chunks, current = [], ""
            for sentence in sentences:
                if current and len(current) + len(sentence) > 450:
                    chunks.append(current)
                    current = sentence
                else:
                    current = f"{current} {sentence}".strip()
            if current:
                chunks.append(current)
            block["paragraphs"] = chunks
    return _expand_paragraphs(blocks)


def _expand_paragraphs(blocks: list[dict]) -> list[dict]:
    result = []
    for block in blocks:
        if "paragraphs" in block:
            result.extend({"type": "paragraph", "text": text} for text in block["paragraphs"])
        else:
            result.append(block)
    return result
