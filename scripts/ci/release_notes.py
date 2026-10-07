#!/usr/bin/env python3
"""Validate the changelog; print the notes for a stable release tag."""

import argparse
import re
import sys
import tomllib
from datetime import date
from pathlib import Path


VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def release_notes(root: Path, tag: str | None) -> str:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = list(re.finditer(r"^## (.+)$", text, re.MULTILINE))
    if not headings or headings[0].group(1) != "[Unreleased]":
        raise ValueError("The first changelog section must be ## [Unreleased]")
    sections = {}
    for index, heading in enumerate(headings):
        title = heading.group(1)
        if index == 0:
            version = "Unreleased"
        else:
            match = re.fullmatch(rf"\[({VERSION})\] - (\d{{4}}-\d{{2}}-\d{{2}})", title)
            if not match:
                raise ValueError(f"Invalid changelog heading: {title}")
            version, released = match.groups()
            date.fromisoformat(released)
        if version in sections:
            raise ValueError(f"Duplicate changelog section: {version}")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        sections[version] = text[heading.end():end].strip()

    if tag is None:
        return ""
    if not re.fullmatch(rf"v{VERSION}", tag):
        raise ValueError("Release tag must be a stable vMAJOR.MINOR.PATCH version")
    version = tag[1:]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["version"] != version:
        raise ValueError(f"Tag {tag} does not match pyproject.toml project.version")
    if version not in sections:
        raise ValueError(f"Missing dated changelog entry for {version}")
    notes = sections[version]
    content = re.sub(r"<!--.*?-->", "", notes, flags=re.DOTALL)
    items = re.findall(r"^- (.+)$", content, re.MULTILINE)
    if not any(item.strip().lower() not in {"todo", "tbd", "none"} for item in items):
        raise ValueError(f"Changelog entry for {version} needs substantive bullet points")
    return notes + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--tag", help="Stable tag to validate and extract, e.g. v0.2.0")
    args = parser.parse_args()
    try:
        notes = release_notes(args.root, args.tag)
    except (ValueError, OSError, KeyError) as error:
        print(f"Release validation failed: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
