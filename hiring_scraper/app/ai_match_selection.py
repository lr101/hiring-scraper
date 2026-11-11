"""Stable static-only selection; AI outcomes never expand the checked pool."""
from collections import Counter
from hashlib import sha256
from math import ceil


def select_cases(rows):
    """Check at most ten or 10% of eligible jobs per profile, whichever is less.

    Reserve roughly 20% for disagreement/uncertainty and cap any employer at two.
    Include already-assessed rows in the input: otherwise repeated passes drain
    the inventory. This is selection arithmetic, not an applicability judgment.
    """
    target = min(10, ceil(len(rows) * .1))
    if not target:
        return []
    priority_count = max(1, int(target * .8))
    selected, employers = [], Counter()
    def take(ordered, limit):
        for row in ordered:
            if len(selected) >= limit:
                break
            if row['id'] in selected or employers[row['company']] >= 2:
                continue
            selected.append(row['id'])
            employers[row['company']] += 1
    take(sorted(rows, key=lambda row: (-row['score'], row['id'])), priority_count)
    take(sorted(rows, key=lambda row: (
        -(row.get('task_score', 0) - row['score']),
        -bool(row.get('uncertain')), sha256(str(row['id']).encode()).hexdigest())), target)
    return selected


def in_search_area(job, preferences):
    """Use the saved board area before spending inference on a vacancy."""
    from hiring_scraper.app.board_scope import geographic_scope
    area = preferences.get('search_area') or {}
    try:
        latitude = float(area.get('latitude', 49.0068705))
        longitude = float(area.get('longitude', 8.4034195))
        radius = float(area.get('radius_km', 15))
    except (TypeError, ValueError):
        latitude, longitude, radius = 49.0068705, 8.4034195, 15
    scope = geographic_scope(job, latitude, longitude, radius,
                             area.get('city'), area.get('country_code') or 'DE')
    include_unknown = (preferences.get('matching_defaults') or {}).get('include_unknown', True) is not False
    return scope['eligible'] and (include_unknown or not scope.get('unknowns'))
