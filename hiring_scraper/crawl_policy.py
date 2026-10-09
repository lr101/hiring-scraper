"""Application-wide public-crawl policy and per-process origin pacing."""
import os
from hiring_scraper.http import OriginPacer

RESPECT_ROBOTS = os.getenv('HIRING_RESPECT_ROBOTS', 'false').strip().casefold() not in {'false', '0', 'no'}
ORIGIN_PACER = OriginPacer()
