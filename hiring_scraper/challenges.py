"""Identify explicit bot or CAPTCHA challenge responses without solving them."""
from __future__ import annotations

import re


_CHALLENGE_MARKERS = (
    "cf-chl-", "challenge-platform", "verify you are human",
    "prove you are human", "checking your browser", "complete the security check",
    "automated requests are not allowed", "automated traffic has been detected",
    "unusual traffic from your computer network",
)
_CHALLENGE_PATTERNS = (
    r"\b(?:captcha|captcha challenge)\s+(?:is\s+)?required\s+to\s+(?:continue|proceed|access)\b",
    r"\b(?:please\s+)?(?:solve|complete|enter)\s+(?:the\s+)?captcha\b",
)


def is_bot_challenge(body: bytes | str, header: str | None = None) -> bool:
    if header and header.casefold() == "challenge":
        return True
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    lowered = text.casefold()
    return (any(marker in lowered for marker in _CHALLENGE_MARKERS)
            or any(re.search(pattern, lowered) for pattern in _CHALLENGE_PATTERNS))
