from __future__ import annotations

import re
import unicodedata


def normalize_exclusion_pattern(value: str) -> str:
    """Return the canonical comparison value stored on an exclusion rule."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("c#", "c sharp").replace("c++", "cplusplus")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())
