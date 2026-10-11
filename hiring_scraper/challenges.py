"""Identify explicit bot or CAPTCHA challenge responses without solving them."""
from __future__ import annotations


_CHALLENGE_MARKERS = (
    "cf-chl-", "cf-mitigated", "challenge-platform", "verify you are human",
    "checking your browser", "unusual traffic", "captcha",
)


def is_bot_challenge(body: bytes | str, header: str | None = None) -> bool:
    if header and header.casefold() == "challenge":
        return True
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    lowered = text.casefold()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)
