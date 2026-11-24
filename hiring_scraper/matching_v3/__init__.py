"""Matcher v3: local-LLM job analysis plus reranker-checked CV evidence."""
from hiring_scraper.job_analysis import VERSION as ANALYSIS_VERSION
from hiring_scraper.matching_v3.evidence import VERSION as EVIDENCE_VERSION
from hiring_scraper.matching_v3.score import match_job_v3
from hiring_scraper.matching_v3.weights import VERSION as SCORE_VERSION

VERSION = f'{SCORE_VERSION}|{ANALYSIS_VERSION}|{EVIDENCE_VERSION}'

__all__ = ['VERSION', 'match_job_v3']
