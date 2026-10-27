"""Evidence-based area eligibility and verified vacancy identity for the board."""
from datetime import datetime, time, timezone
from functools import lru_cache
import json
import re
import unicodedata
from importlib.resources import files
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from hiring_scraper.geography import GERMAN_STATES, haversine_m, state_for_point

_COUNTRIES = {'germany':'DE', 'deutschland':'DE', 'united states':'US', 'united states of america':'US',
              'usa':'US', 'united kingdom':'GB', 'uk':'GB', 'france':'FR', 'switzerland':'CH',
              'schweiz':'CH', 'austria':'AT', 'österreich':'AT', 'spain':'ES', 'españa':'ES',
              'canada':'CA', 'kanada':'CA', 'australia':'AU', 'australien':'AU',
              'netherlands':'NL', 'niederlande':'NL', 'italy':'IT', 'italien':'IT',
              'belgium':'BE', 'belgien':'BE', 'poland':'PL', 'polen':'PL', 'ireland':'IE',
              'irland':'IE', 'india':'IN', 'indien':'IN', 'china':'CN', 'japan':'JP',
              'brazil':'BR', 'brasilien':'BR', 'portugal':'PT', 'sweden':'SE', 'schweden':'SE',
              'denmark':'DK', 'dänemark':'DK', 'norway':'NO', 'norwegen':'NO', 'finland':'FI',
              'finnland':'FI', 'mexico':'MX', 'mexiko':'MX', 'new zealand':'NZ', 'neuseeland':'NZ'}
_TEXT_COUNTRY_CODES = {'DE', 'US', 'UK', 'GB', 'FR', 'CH', 'AT', 'ES', 'CA', 'AU', 'NL', 'IT',
                       'BE', 'PL', 'IE', 'IN', 'CN', 'JP', 'BR', 'PT', 'SE', 'DK', 'NO', 'FI',
                       'MX', 'NZ'}
_STATE_TEXT_ALIASES = {
    'baden wurttemberg': 'Baden-Württemberg', 'baden wuerttemberg': 'Baden-Württemberg',
    'bavaria': 'Bayern', 'north rhine westphalia': 'Nordrhein-Westfalen',
    'north rhine-westphalia': 'Nordrhein-Westfalen', 'lower saxony': 'Niedersachsen',
    'hesse': 'Hessen', 'saxony': 'Sachsen', 'saxony anhalt': 'Sachsen-Anhalt',
    'thuringia': 'Thüringen', 'rhineland palatinate': 'Rheinland-Pfalz',
    'mecklenburg western pomerania': 'Mecklenburg-Vorpommern',
    'schleswig holstein': 'Schleswig-Holstein',
}
_GERMAN_CITIES_BY_STATE = {
    'Baden-Württemberg': (
        'Karlsruhe', 'Stuttgart', 'Mannheim', 'Freiburg', 'Heidelberg', 'Ulm',
        'Heilbronn', 'Pforzheim', 'Reutlingen', 'Tübingen', 'Baden-Baden',
        'Konstanz', 'Esslingen', 'Ludwigsburg', 'Offenburg',
    ),
    'Bayern': (
        'München', 'Munich', 'Nürnberg', 'Nuremberg', 'Augsburg', 'Regensburg',
        'Ingolstadt', 'Würzburg', 'Fürth', 'Erlangen', 'Bamberg', 'Bayreuth',
        'Landshut', 'Passau', 'Rosenheim', 'Kempten',
    ),
    'Berlin': ('Berlin',),
    'Brandenburg': ('Potsdam', 'Cottbus', 'Brandenburg an der Havel',
                    'Frankfurt Oder', 'Frankfurt an der Oder'),
    'Bremen': ('Bremen', 'Bremerhaven'),
    'Hamburg': ('Hamburg',),
    'Hessen': (
        'Frankfurt', 'Frankfurt am Main', 'Wiesbaden', 'Kassel', 'Darmstadt',
        'Offenbach', 'Hanau', 'Marburg', 'Gießen', 'Giessen',
    ),
    'Mecklenburg-Vorpommern': ('Rostock', 'Schwerin', 'Greifswald', 'Stralsund'),
    'Niedersachsen': (
        'Hannover', 'Braunschweig', 'Osnabrück', 'Osnabrueck', 'Oldenburg',
        'Wolfsburg', 'Göttingen', 'Goettingen', 'Hildesheim', 'Salzgitter',
    ),
    'Nordrhein-Westfalen': (
        'Köln', 'Cologne', 'Düsseldorf', 'Duesseldorf', 'Dortmund', 'Essen',
        'Duisburg', 'Bochum', 'Wuppertal', 'Bonn', 'Münster', 'Muenster',
        'Bielefeld', 'Aachen', 'Gelsenkirchen', 'Krefeld', 'Halle Westf', 'Halle Westfalen',
    ),
    'Rheinland-Pfalz': ('Mainz', 'Ludwigshafen', 'Koblenz', 'Trier', 'Kaiserslautern'),
    'Saarland': ('Saarbrücken', 'Saarbruecken'),
    'Sachsen': ('Dresden', 'Leipzig', 'Chemnitz', 'Zwickau', 'Görlitz', 'Goerlitz'),
    'Sachsen-Anhalt': ('Magdeburg', 'Halle', 'Dessau-Roßlau', 'Dessau-Rosslau'),
    'Schleswig-Holstein': (
        'Kiel', 'Lübeck', 'Luebeck', 'Flensburg', 'Neumünster', 'Neumuenster',
        'Oldenburg in Holstein', 'Oldenburg Holstein',
    ),
    'Thüringen': ('Erfurt', 'Jena', 'Gera', 'Weimar', 'Gotha'),
}
_CITY_NAME_GROUPS = (
    ('München', 'Munich'), ('Nürnberg', 'Nuremberg'), ('Köln', 'Cologne'),
    ('Düsseldorf', 'Duesseldorf'), ('Münster', 'Muenster'),
    ('Saarbrücken', 'Saarbruecken'), ('Frankfurt am Main', 'Frankfurt'),
)


def german_location_text_names(state=None):
    """Return German state and common city names used for bounded SQL candidates."""
    if state:
        return tuple(dict.fromkeys((state, *(
            alias for alias, canonical in _STATE_TEXT_ALIASES.items() if canonical == state
        ), *_GERMAN_CITIES_BY_STATE.get(state, ()))))
    return tuple(dict.fromkeys((
        *GERMAN_STATES, *_STATE_TEXT_ALIASES.keys(),
        *(city for cities in _GERMAN_CITIES_BY_STATE.values() for city in cities),
    )))


def _location_words(value):
    if not isinstance(value, str):
        return ''
    normalized = unicodedata.normalize('NFKD', value.casefold().replace('ß', 'ss'))
    normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
    return ' '.join(re.findall(r'[a-z0-9]+', normalized))


def _location_word_variants(value):
    if not isinstance(value, str):
        return frozenset()
    return _cached_location_word_variants(value)


@lru_cache(maxsize=4096)
def _cached_location_word_variants(value):
    # The same workplace labels and static aliases occur across many jobs.
    # Immutable results keep the bounded process-local cache safe to share.
    source = value.casefold().replace('ß', 'ss')
    transliterated = source.translate(str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue'}))
    return frozenset((_location_words(source), _location_words(transliterated)))


@lru_cache(maxsize=1)
def _german_state_features():
    """Load the bundled BKG state boundaries used to classify workplace points."""
    try:
        source = files(__package__).joinpath('german_states.json').read_text(encoding='utf-8')
        return tuple(json.loads(source).get('features', ()))
    except (OSError, json.JSONDecodeError):
        return ()


@lru_cache(maxsize=1)
def german_state_bounds():
    """Return WGS84 bounding boxes keyed by canonical German state name."""
    bounds = {}
    for feature in _german_state_features():
        properties = feature.get('properties', {})
        try:
            state = next(name for name in GERMAN_STATES if name == properties.get('gen'))
            west, south, east, north = feature['bbox']
        except (AttributeError, KeyError, TypeError, ValueError, StopIteration):
            continue
        if state in bounds:
            old_west, old_south, old_east, old_north = bounds[state]
            bounds[state] = (min(old_west, west), min(old_south, south),
                             max(old_east, east), max(old_north, north))
        else:
            bounds[state] = (west, south, east, north)
    return bounds


def _location_state(location_country, latitude, longitude):
    if location_country not in {None, 'DE'} or latitude is None or longitude is None:
        return None
    try:
        longitude, latitude = float(longitude), float(latitude)
        features = [feature for feature in _german_state_features()
                    if len(feature.get('bbox', ())) == 4
                    and feature['bbox'][0] <= longitude <= feature['bbox'][2]
                    and feature['bbox'][1] <= latitude <= feature['bbox'][3]]
        return state_for_point(latitude, longitude, features)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=4096)
def location_text_names(target):
    """Return configured city names and their common feed spelling aliases."""
    target_variants = _location_word_variants(target)
    names = {target}
    for group in _CITY_NAME_GROUPS:
        group_variants = {variant for name in group for variant in _location_word_variants(name)}
        if target_variants & group_variants:
            names.update(group)
    return tuple(names)


def location_text_matches(value, target):
    """Match a whole city or region name in a feed's free-text work location."""
    value_variants = _location_word_variants(value)
    target_variants = {variant for name in location_text_names(target)
                       for variant in _location_word_variants(name)}
    frankfurt_oder = any(
        phrase in f' {words} '
        for words in value_variants
        for phrase in (' frankfurt oder ', ' frankfurt an der oder ')
    )
    halle_westfalen = any(' halle westf ' in f' {words} ' for words in value_variants)
    halle_westfalen = halle_westfalen or any(' halle westfalen ' in f' {words} ' for words in value_variants)
    oldenburg_holstein = any(
        phrase in f' {words} '
        for words in value_variants
        for phrase in (' oldenburg in holstein ', ' oldenburg holstein ')
    )
    return any(target_words and f' {target_words} ' in f' {words} '
               and not (frankfurt_oder and target_words == 'frankfurt')
               and not (halle_westfalen and target_words == 'halle')
               and not (oldenburg_holstein and target_words == 'oldenburg')
               for words in value_variants for target_words in target_variants)


def _work_location_text_evidence(job):
    """Return independent text/code pairs for each listed work location."""
    locations = list(job.locations or ())
    raw_values = []
    if isinstance(job.location_text, str):
        raw_values = [part.strip() for part in re.split(r'[;\n|]+|(?<=\s)/(?=\s)', job.location_text)
                      if part.strip()]
    evidence = []
    for value in raw_values:
        matched_location = next((item for item in locations
                                 if isinstance(item.label, str) and item.label.strip()
                                 and (location_text_matches(value, item.label)
                                      or location_text_matches(item.label, value))), None)
        if matched_location is None and len(locations) == 1:
            matched_location = locations[0]
        code = country_code(matched_location.country_code) if matched_location else None
        coordinates = (matched_location.latitude, matched_location.longitude) if matched_location else (None, None)
        evidence.append((value, code, *coordinates))
    evidence.extend((item.label.strip() if isinstance(item.label, str) else '',
                     country_code(item.country_code), item.latitude, item.longitude)
                    for item in locations
                    if (isinstance(item.label, str) and item.label.strip())
                    or item.latitude is not None or item.longitude is not None)
    return list(dict.fromkeys(evidence))


def location_text_countries(value):
    """Return countries named in a work-location string, including short country codes."""
    words = _location_words(value)
    codes = {code for name, code in _COUNTRIES.items() if location_text_matches(words, name)}
    if isinstance(value, str):
        standalone_code = value.strip().upper()
        if standalone_code in _TEXT_COUNTRY_CODES:
            codes.add(standalone_code)
        for match in re.finditer(r'(?<![A-Za-z0-9])([A-Z]{2})(?![A-Za-z0-9])', value):
            token = match.group(1)
            if token in _TEXT_COUNTRY_CODES:
                codes.add(token)
        for match in re.finditer(r'[,(/]\s*([a-z]{2})(?![A-Za-z0-9])', value):
            token = match.group(1).upper()
            if token in _TEXT_COUNTRY_CODES:
                codes.add(token)
    return codes


def location_text_states(value):
    """Return German states explicitly named in a work-location string."""
    phrases_by_state = {state: [state] for state in GERMAN_STATES}
    for phrase, state in _STATE_TEXT_ALIASES.items():
        phrases_by_state[state].append(phrase)
    for state, cities in _GERMAN_CITIES_BY_STATE.items():
        phrases_by_state[state].extend(cities)

    occurrences = []
    for state, phrases in phrases_by_state.items():
        for text_variant in _location_word_variants(value):
            haystack = f' {text_variant} '
            for phrase in phrases:
                for phrase_variant in _location_word_variants(phrase):
                    if not phrase_variant:
                        continue
                    needle = f' {phrase_variant} '
                    start = 0
                    while (index := haystack.find(needle, start)) >= 0:
                        occurrences.append((text_variant, index, index + len(needle), state,
                                            len(phrase_variant.split())))
                        start = index + 1
    states = set()
    for text_variant, start, end, state, phrase_length in occurrences:
        shadowed = any(
            other_text == text_variant and other_state != state and other_length > phrase_length
            and other_start <= start and other_end >= end
            for other_text, other_start, other_end, other_state, other_length in occurrences
        )
        if not shadowed:
            states.add(state)
    return states


def _location_text_is_unspecified(value):
    words = _location_words(value)
    if words in {
        'remote', 'fully remote', 'hybrid', 'onsite', 'on site', 'office',
        'flexible', 'multiple locations', 'various locations', 'work from home',
        'home office', 'field based', 'field work', 'anywhere',
    }:
        return True
    tokens = set(words.split())
    return bool(tokens and tokens <= {
        'remote', 'fully', 'hybrid', 'onsite', 'on', 'site', 'office',
        'flexible', 'multiple', 'various', 'locations', 'location', 'work',
        'from', 'home', 'field', 'based', 'role', 'possible', 'available',
    })


def vacancy_text_scope(job, *, place=None, state=None, country=None):
    """Use meaningful vacancy locations as authoritative scope evidence.

    None signals that the location is missing or generic, so callers may use
    their existing employer-location fallback.
    """
    evidence = _work_location_text_evidence(job)
    meaningful = [(value, code, latitude, longitude) for value, code, latitude, longitude in evidence
                  if not _location_text_is_unspecified(value)]
    if place:
        if not meaningful:
            return None
        target_country = country_code(country)
        eligible_values = []
        for value, location_country, latitude, longitude in meaningful:
            text_countries = location_text_countries(value)
            if target_country and text_countries and target_country not in text_countries:
                continue
            if target_country and not text_countries and location_country and target_country != location_country:
                continue
            eligible_values.append(value)
            if location_text_matches(value, place):
                return True
        # A different city name can still be within a configured radius. Let
        # precise work-location coordinates decide that case; without them the
        # meaningful text rules the job out of this city.
        if eligible_values and any(location.latitude is not None and location.longitude is not None
               for location in job.locations):
            return None
        return False
    if country:
        target_country = country_code(country)
        unresolved = False
        for value, location_country, latitude, longitude in meaningful:
            text_countries = location_text_countries(value)
            if target_country in text_countries:
                return True
            if text_countries:
                continue
            if location_text_states(value):
                if target_country == 'DE' and location_country in {None, 'DE'}:
                    return True
                if location_country == target_country:
                    return True
                continue
            if location_country:
                if target_country == location_country:
                    return True
                continue
            if target_country == 'DE' and _location_state(location_country, latitude, longitude) is not None:
                return True
            unresolved = True
        return False if unresolved or meaningful else None
    if state:
        unresolved = False
        for value, location_country, latitude, longitude in meaningful:
            text_countries = location_text_countries(value)
            if text_countries and 'DE' not in text_countries:
                continue
            workplace_state = _location_state(location_country, latitude, longitude)
            if workplace_state:
                if workplace_state == state:
                    return True
                continue
            text_states = location_text_states(value)
            if state in text_states and location_country in {None, 'DE'}:
                return True
            if text_states:
                continue
            if location_country and location_country != 'DE':
                continue
            unresolved = True
        return False if unresolved or meaningful else None
    return None


def country_code(value):
    if isinstance(value, dict):
        for key in ('addressCountry', 'country_code', 'name', 'address'):
            if code := country_code(value.get(key)):
                return code
        return None
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


def _remote_scope(job):
    """Office addresses do not establish applicant permission to work remotely."""
    if not fully_remote(job):
        return set(), False
    raw = job.raw_metadata or {}
    for key in ('remote_country_codes', 'applicantLocationRequirements',
                'applicant_location_requirements', 'remote_country'):
        values = raw.get(key)
        # Producers use missing/null fields and empty country lists for absent scope.
        # Other present values must parse, including falsey malformed objects/scalars.
        if values is None or isinstance(values, list) and not values:
            continue
        values = values if isinstance(values, list) else [values]
        codes = {code for item in values if (code := country_code(item))}
        return codes, not bool(codes)
    if raw.get('remote_scope_source'):
        codes = {code for location in job.locations if (code := country_code(location.country_code))}
        for key in ('country_code', 'country', 'office_country_code'):
            if code := country_code(raw.get(key)):
                codes.add(code)
        office = raw.get('office')
        if isinstance(office, dict):
            if code := country_code(office.get('country_code') or office.get('country')):
                codes.add(code)
        return codes, False
    return set(), False


def remote_countries(job):
    return _remote_scope(job)[0]


def geographic_scope(job, latitude, longitude, radius_km, place=None, country=None, *, include_expired=False):
    country = country_code(country)
    if not include_expired and expiry(job)['expired']:
        return {'eligible': False, 'reason': 'expired', 'unknowns': [], 'match_kind': None}
    if fully_remote(job):
        countries, unresolved_restriction = _remote_scope(job)
        if country and (unresolved_restriction or countries and country not in countries):
            return {'eligible': False, 'reason': 'remote_country', 'unknowns': [], 'match_kind': 'remote'}
        gaps = ['Remote country eligibility not stated'] if country and not countries else []
        return {'eligible': True, 'reason': None, 'unknowns': gaps, 'match_kind': 'remote'}
    text_scope = vacancy_text_scope(job, place=place, country=country)
    if text_scope is not None:
        return {'eligible': text_scope, 'reason': None if text_scope else 'outside_area',
                'unknowns': [], 'match_kind': 'in_area' if text_scope else None}
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
    identity_keys = {'id', 'ref', 'jobid', 'job_id', 'job-id', 'gh_jid', 'gh_job', 'jid',
                     'requisitionid', 'requisition_id', 'reqid', 'req_id', 'vacancyid',
                     'vacancy_id', 'positionid', 'position_id', 'reference', 'referenznummer'}
    has_identity = any(key.casefold() in identity_keys and value.strip() for key, value in query)
    if path.rsplit('/', 1)[-1].casefold() in generic and not has_identity and not fragment:
        return None
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path,
                       urlencode(sorted(query)), fragment))


def vacancy_keys(job):
    keys = [('source', job.feed_id, job.external_id)]
    if (group_id := getattr(job, 'vacancy_group_id', None)) is not None:
        keys.append(('retained-vacancy', group_id))
    if url := canonical_vacancy_url(job.url):
        keys.append(('url', url))
    employer_id = getattr(job, 'employer_company_id', None)
    company = getattr(getattr(job, 'feed', None), 'company', None)
    if employer_id is None and company is not None and company.category != 'job_board_source' and company.source not in {
            'default_job_feed', 'managed_feed'}:
        employer_id = company.id
    requisition = (job.raw_metadata or {}).get('requisition_id')
    if requisition not in (None, '') and isinstance(requisition, (str, int)):
        # Company identity keeps existing Greenhouse language-variant behavior.
        keys.append(('requisition', job.feed.provider, 'company', job.feed.company_id, str(requisition)))
        if job.feed.tenant:
            keys.append(('requisition', job.feed.provider, 'tenant', job.feed.tenant, str(requisition)))
        if employer_id is not None:
            keys.append(('requisition', 'employer', employer_id, str(requisition)))
    if employer_id is not None and isinstance(job.date_posted, str) and job.date_posted:
        title = _vacancy_text(job.title)
        location = _vacancy_text(job.location_text or '; '.join(
            item.label for item in getattr(job, 'locations', []) if item.label))
        if (len(title) >= 8 and location and title not in {
                'initiativbewerbung', 'general application', 'unsolicited application', 'talent pool'}):
            keys.append(('employer-vacancy', employer_id, title, location, job.date_posted[:10]))
    return keys


def _vacancy_text(value):
    if not isinstance(value, str):
        return ''
    normalized = unicodedata.normalize('NFKD', value.casefold().replace('ß', 'ss'))
    normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r'\b(?:deutschland|germany)\b', ' ', normalized)
    return ' '.join(re.findall(r'[a-z0-9]+', normalized))


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
        return (job.is_active and not expiry(job)['expired'], len(job.description or ''),
                len(job.locations), -job.id)
    return [max(group, key=richness) for group in groups.values()]
