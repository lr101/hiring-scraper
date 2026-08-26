from __future__ import annotations

import re
import unicodedata
from typing import Protocol


class _PlaceForLabel(Protocol):
    name: str
    postal_code: str
    admin_area: str
    latitude: float
    longitude: float


def normalize_place_text(value: str) -> str:
    """Normalize German city and postal-code search text without changing stored display text."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def format_place_label(place: _PlaceForLabel) -> str:
    locality = ", ".join(part for part in [place.postal_code, place.admin_area] if part)
    coordinates = f"{place.latitude:.4f}, {place.longitude:.4f}"
    return (
        f"{place.name} ({locality}; {coordinates})" if locality else f"{place.name} ({coordinates})"
    )
