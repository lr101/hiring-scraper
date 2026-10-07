"""Evidence-based area eligibility and verified vacancy identity for the board."""
from datetime import datetime, time, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from hiring_scraper.geography import haversine_m

_COUNTRIES = {'germany':'DE', 'deutschland':'DE', 'united states':'US', 'united states of america':'US',
              'usa':'US', 'united kingdom':'GB', 'uk':'GB', 'france':'FR', 'switzerland':'CH',
              'schweiz':'CH', 'austria':'AT', 'österreich':'AT', 'spain':'ES', 'españa':'ES'}


def country_code(value):
    if isinstance(value, dict):
        value = value.get('addressCountry') or value.get('name') or value.get('country_code')
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value.upper() if len(value) == 2 and value.isalpha() else _COUNTRIES.get(value.casefold())


def expiry(job):
    metadata = job.raw_metadata or {}
    value = metadata.get('validThrough') or metadata.get('valid_through')
    expired = False
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if len(value) == 10:
                parsed = datetime.combine(parsed.date(), time.max)
            parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            expired = parsed < datetime.now(timezone.utc)
        except ValueError:
            pass
    return {'valid_through': value if isinstance(value, str) else None, 'expired': expired}


def fully_remote(job):
    return job.work_arrangement in {None, 'remote'} and (job.is_remote or job.work_arrangement == 'remote')


def remote_countries(job):
    """Source scope only applies to explicit full remote; no text guessing."""
    if not fully_remote(job):
        return set()
    raw = job.raw_metadata or {}
    for key in ('remote_country_codes', 'applicantLocationRequirements', 'applicant_location_requirements'):
        values = raw.get(key)
        if values:
            values = values if isinstance(values, list) else [values]
            codes = {code for item in values if (code := country_code(item))}
            if codes:
                return codes
    # An explicitly country-scoped remote office remains country-scoped.
    codes = {code for location in job.locations if (code := country_code(location.country_code))}
    for key in ('country_code', 'country', 'office_country_code', 'remote_country'):
        if code := country_code(raw.get(key)):
            codes.add(code)
    office = raw.get('office')
    if isinstance(office, dict):
        if code := country_code(office.get('country_code') or office.get('country')):
            codes.add(code)
    return codes


def geographic_scope(job, latitude, longitude, radius_km, place=None, country=None):
    country = country_code(country)
    if expiry(job)['expired']:
        return {'eligible': False, 'reason': 'expired', 'unknowns': [], 'match_kind': None}
    if fully_remote(job):
        countries = remote_countries(job)
        if country and countries and country not in countries:
            return {'eligible': False, 'reason': 'remote_country', 'unknowns': [], 'match_kind': 'remote'}
        gaps = ['Remote country eligibility not stated'] if country and not countries else []
        return {'eligible': True, 'reason': None, 'unknowns': gaps, 'match_kind': 'remote'}
    place_key = place.strip().casefold() if place else None
    for location in job.locations:
        location_country = country_code(location.country_code)
        if country and location_country and country != location_country:
            continue
        # A partial coordinate is not evidence that the city-text fallback is safe.
        if location.latitude is not None or location.longitude is not None:
            if location.latitude is not None and location.longitude is not None and latitude is not None and longitude is not None:
                if haversine_m(latitude, longitude, location.latitude, location.longitude) <= radius_km * 1000:
                    return {'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'in_area'}
        elif place_key and location.label.split(',', 1)[0].strip().casefold() == place_key:
            return {'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'in_area'}
    return {'eligible': False, 'reason': 'outside_area', 'unknowns': [], 'match_kind': None}


def canonical_vacancy_url(url):
    try:
        parsed = urlsplit(url)
    except (ValueError, TypeError):
        return None
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return None
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.casefold().startswith('utm_') and key.casefold() not in
             {'gh_src', 'gclid', 'fbclid', 'msclkid', 'trk'}]
    path = parsed.path.rstrip('/') or '/'
    fragment = parsed.fragment
    if fragment.casefold() in {'top', 'jobs', 'careers', 'main', 'content'}:
        fragment = ''
    generic = {'', 'jobs', 'job', 'careers', 'career', 'stellenangebote', 'stellen', 'vacancies',
               'openings', 'positions', 'open-positions', 'job-openings', 'all-jobs', 'join-us',
               'careers.html', 'career.html', 'jobs.html', 'index.html', 'index.php'}
    if path.rsplit('/', 1)[-1].casefold() in generic and not query and not fragment:
        return None
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path,
                       urlencode(sorted(query)), fragment))


def vacancy_keys(job):
    keys = [('source', job.feed_id, job.external_id)]
    if url := canonical_vacancy_url(job.url):
        keys.append(('url', url))
    requisition = (job.raw_metadata or {}).get('requisition_id')
    if requisition not in (None, '') and isinstance(requisition, (str, int)):
        # Company identity keeps existing Greenhouse language-variant behavior.
        keys.append(('requisition', job.feed.provider, 'company', job.feed.company_id, str(requisition)))
        if job.feed.tenant:
            keys.append(('requisition', job.feed.provider, 'tenant', job.feed.tenant, str(requisition)))
    return keys


def deduplicate_jobs(jobs):
    groups, seen = {}, {}
    for job in jobs:
        keys = vacancy_keys(job)
        existing = {seen[key] for key in keys if key in seen}
        group = min(existing) if existing else job.id
        combined = [job]
        for old_group in existing:
            combined.extend(groups.pop(old_group, []))
        groups[group] = combined
        for candidate in combined:
            for key in vacancy_keys(candidate):
                seen[key] = group
    def richness(job):
        timestamp = job.last_seen_at.timestamp() if job.last_seen_at else 0
        return (job.is_active and not expiry(job)['expired'], len(job.description or ''),
                len(job.locations), timestamp, -job.id)
    return [max(group, key=richness) for group in groups.values()]
