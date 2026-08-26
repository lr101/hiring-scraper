from __future__ import annotations

import re
import unicodedata


def normalize_place_text(value: str) -> str:
    """Normalize German city and postal-code search text without changing stored display text."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())
