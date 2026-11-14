"""Full-coverage AI candidate ordering and saved geographic scope."""


def select_cases(rows):
    """Prioritize stronger local evidence without excluding any candidate."""
    return [row['id'] for row in sorted(rows, key=lambda row: (-row['score'], row['id']))]


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
