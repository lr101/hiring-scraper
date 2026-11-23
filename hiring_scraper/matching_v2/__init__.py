"""v2 deterministic gate + additive feature + monotone-calibrated job matcher."""
from __future__ import annotations

from hiring_scraper.matching_v2.score import match_job_v2
from hiring_scraper.matching_v2.weights import VERSION

__all__ = ['VERSION', 'match_job_v2']
