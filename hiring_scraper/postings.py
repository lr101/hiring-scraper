"""Source-independent rules for combining parsed posting rows before they are saved.

Every adapter returns rows keyed by ``id`` and the workers upsert by that id. Two
rows with one id are one posting read twice (structured data plus page HTML, a
card plus its detail page, a self-link). Saving them in turn would let whichever
came last overwrite the other, so they are merged field by field instead.
"""
from __future__ import annotations

_STRUCTURED_METHODS = frozenset({"schema_org_jobposting", "schema_org_microdata"})
_EMPTY = (None, "", [], {})


def _method(row: dict) -> str | None:
    metadata = row.get("raw_metadata")
    return metadata.get("extraction_method") if isinstance(metadata, dict) else None


def _location_quality(locations) -> tuple[int, int]:
    if not isinstance(locations, list) or not locations:
        return (0, 0)
    detailed = sum(1 for item in locations if isinstance(item, dict) and
                   (item.get("country_code") or item.get("latitude") is not None))
    return (1 if detailed else 0, len(locations))


def _rank(row: dict) -> tuple[int, int]:
    """Structured source data first, then the row that says the most."""
    filled = sum(1 for value in row.values() if value not in _EMPTY)
    return (1 if _method(row) in _STRUCTURED_METHODS else 0, filled)


def merge_posting_rows(rows: list[dict]) -> dict:
    """One row from several readings of the same posting."""
    ordered = sorted(rows, key=_rank, reverse=True)
    merged = dict(ordered[0])
    metadata = dict(merged.get("raw_metadata") or {})
    for row in ordered[1:]:
        for field, value in row.items():
            if value in _EMPTY or field in {"id", "raw_metadata"}:
                continue
            if merged.get(field) in _EMPTY:
                merged[field] = value
        extra = row.get("raw_metadata")
        if isinstance(extra, dict):
            metadata = {**extra, **metadata}
    best_locations = max((row.get("locations") for row in ordered), key=_location_quality)
    if _location_quality(best_locations) > _location_quality(merged.get("locations")):
        merged["locations"] = best_locations
        labels = [item.get("label") for item in best_locations if isinstance(item, dict) and item.get("label")]
        if labels:
            merged["location"] = "; ".join(labels)
    # Prefer the structured description; otherwise keep the fullest reading.
    if _method(ordered[0]) not in _STRUCTURED_METHODS or not ordered[0].get("description"):
        descriptions = [row.get("description") for row in ordered if isinstance(row.get("description"), str)]
        if descriptions:
            merged["description"] = max(descriptions, key=len)
    metadata["merged_source_rows"] = len(rows)
    merged["raw_metadata"] = metadata
    return merged


def merge_duplicate_postings(rows: list[dict]) -> list[dict]:
    """Collapse rows sharing an id, keeping first-seen order; rows without an id pass through."""
    groups: dict[str, list[dict]] = {}
    order: list[tuple[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = row.get("id")
        if key in (None, ""):
            order.append(("row", row))
            continue
        key = str(key)
        if key not in groups:
            groups[key] = []
            order.append(("id", key))
        groups[key].append(row)
    result = []
    for kind, value in order:
        if kind == "row":
            result.append(value)
        else:
            group = groups[value]
            result.append(group[0] if len(group) == 1 else merge_posting_rows(group))
    return result
