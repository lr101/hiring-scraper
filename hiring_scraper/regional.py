"""Opt-in adapter for the observed public BA Jobsuche endpoint.

This is an observed interface, not a guaranteed official API contract. Searches
are selected and bounded; their absence is never evidence that a job closed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from hiring_scraper.http import Client, OriginPacer, RequestBudget

BASE_URL = 'https://rest.arbeitsagentur.de/jobboerse/jobsuche-service'
SEARCH_PATH = '/jobboerse/jobsuche-service/pc/v6/jobs'
DETAIL_PATH = '/jobboerse/jobsuche-service/pc/v4/jobdetails/'
DEFAULT_TERMS = ('Projektmanager', 'Projektkoordinator', 'PMO', 'Produktmanager',
                 'Produktentwicklung', 'Prozessoptimierung', 'Personalentwicklung',
                 'Schulungskoordinator', 'Program Coordinator', 'Project Coordinator',
                 'Business Analyst', 'Product Operations', 'UX Designer', 'Training Coordinator')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _coordinate(value, limit):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and -limit <= result <= limit else None


def _country(value):
    label = _text(value).upper()
    countries = {'DEUTSCHLAND': 'DE', 'GERMANY': 'DE', 'ÖSTERREICH': 'AT', 'OESTERREICH': 'AT', 'AUSTRIA': 'AT',
                 'SCHWEIZ': 'CH', 'SWITZERLAND': 'CH', 'FRANKREICH': 'FR', 'FRANCE': 'FR'}
    return countries.get(label, label if re.fullmatch('[A-Z]{2}', label) else None)


def normalize_job(row: dict, observed_at: str | None = None) -> dict:
    """Normalize one source row while retaining complete source text and evidence."""
    if not isinstance(row, dict):
        raise ValueError('A source job must be an object')
    reference = _text(row.get('referenznummer') or row.get('refnr'))
    title = _text(row.get('stellenangebotsTitel') or row.get('titel'))
    company = _text(row.get('firma') or row.get('arbeitgeber'))
    if not reference or not title or not company:
        raise ValueError('Source reference, title and employer are required')
    employer_hash = _text(row.get('arbeitgeberKundennummerHash'))
    normalized_name = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', company).casefold())
    company_id = 'hash:' + employer_hash if employer_hash else 'name:' + hashlib.sha256(normalized_name.encode()).hexdigest()
    locations = []
    raw_locations = row.get('stellenlokationen') or row.get('arbeitsorte') or row.get('arbeitsort') or []
    if isinstance(raw_locations, dict):
        raw_locations = [raw_locations]
    if not isinstance(raw_locations, list):
        raw_locations = []
    for location in raw_locations:
        if not isinstance(location, dict):
            continue
        address = location.get('adresse') if isinstance(location.get('adresse'), dict) else location
        coordinates = location.get('koordinaten') if isinstance(location.get('koordinaten'), dict) else {}
        latitude = _coordinate(location.get('breite', coordinates.get('lat')), 90)
        longitude = _coordinate(location.get('laenge', coordinates.get('lon')), 180)
        if latitude is None or longitude is None:
            latitude = longitude = None
        label = ', '.join(x for x in [_text(address.get('ort')), _text(address.get('plz'))] if x)
        country = _country(address.get('land'))
        if not label:
            label = _text(address.get('land')) or 'Location unspecified'
        locations.append({'label': label, 'latitude': latitude, 'longitude': longitude,
                          'precision': 'source_coordinates' if latitude is not None else None, 'country_code': country})
    possible = row.get('homeofficemoeglich') is True
    percentage = _coordinate(row.get('homeofficeprozent'), 100)
    remote = row.get('homeofficetyp') == 'ANGABE_IN_PROZENT' and percentage == 100
    scope_source = _text(row.get('remote_scope_source')) or None
    scope_codes = row.get('remote_country_codes', []) if scope_source else []
    scope_codes = sorted(set(filter(None, (_country(x) for x in scope_codes)))) if isinstance(scope_codes, list) else []
    agency = row.get('istPrivateArbeitsvermittlung') is True or row.get('istArbeitnehmerUeberlassung') is True
    salary_fields = {key: row[key] for key in ('festgehalt', 'gehaltsspanneVon', 'gehaltsspanneBis',
                                             'verguetungsangabe', 'artDerVerguetung', 'waehrung') if row.get(key) is not None}
    amounts = [str(salary_fields[key]) for key in ('festgehalt', 'gehaltsspanneVon', 'gehaltsspanneBis') if key in salary_fields]
    salary = ' – '.join(amounts) if amounts else None
    if salary:
        salary = ' '.join([salary] + [str(salary_fields[key]) for key in ('verguetungsangabe', 'waehrung') if key in salary_fields])
    publication = row.get('veroeffentlichungszeitraum')
    publication = publication if isinstance(publication, dict) else {}
    observed = row.get('_checked_at') or observed_at or _now()
    source_url = 'https://www.arbeitsagentur.de/jobsuche/jobdetail/' + quote(reference, safe='')
    metadata = {'source': 'arbeitsagentur', 'source_reference': reference, 'source_url': source_url,
                'source_observed_at': observed, 'source_api_url': row.get('_detail_url') or row.get('_search_url'),
                'external_source_url': row.get('externeURL'), 'employer_hash': employer_hash or None,
                'employer_type': 'agency' if agency else 'unverified_employer',
                'is_private_agency': row.get('istPrivateArbeitsvermittlung'),
                'is_staffing_agency': row.get('istArbeitnehmerUeberlassung'),
                'homeoffice_possible': possible, 'homeoffice_percentage': percentage,
                'homeoffice_type': row.get('homeofficetyp'), 'remote_country_codes': scope_codes,
                'remote_scope_source': scope_source, 'salary_evidence': salary_fields,
                'publication_expires_at': publication.get('bis'), 'contract_end': row.get('befristetBis'),
                'search_terms': row.get('_search_terms', []), 'detail_state': row.get('_detail_state', 'captured' if row.get('stellenangebotsBeschreibung') else 'summary'),
                'source_record': dict(row)}
    employment = []
    if row.get('arbeitszeitVollzeit'):
        employment.append('full_time')
    if any(value is True for key, value in row.items() if key.startswith('arbeitszeitTeilzeit')):
        employment.append('part_time')
    return {'external_id': reference, 'title': title, 'company_name': company, 'company_source_id': company_id,
            'url': source_url, 'description': row.get('stellenangebotsBeschreibung') if isinstance(row.get('stellenangebotsBeschreibung'), str) else None,
            'locations': locations, 'location_text': '; '.join(x['label'] for x in locations) or None,
            'is_remote': remote, 'work_arrangement': 'remote' if remote else 'hybrid' if possible else None,
            'employment_type': ', '.join(employment) or None,
            'date_posted': row.get('datumErsteVeroeffentlichung') or publication.get('von'),
            'salary': salary, 'raw_metadata': metadata}


def parse_search(payload: dict) -> dict:
    """Support observed ergebnisliste and older stellenangebote responses."""
    if not isinstance(payload, dict):
        raise ValueError('Search response must be an object')
    rows = payload.get('ergebnisliste', payload.get('stellenangebote'))
    if not isinstance(rows, list):
        raise ValueError('Search response has no recognized result list')
    jobs, invalid = [], 0
    for row in rows:
        try:
            jobs.append(normalize_job(row))
        except ValueError:
            invalid += 1
    total = payload.get('maxErgebnisse', payload.get('maxErgebnis', payload.get('gesamtanzahl')))
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        total = None
    return {'jobs': jobs, 'rows': rows, 'invalid_count': invalid, 'total': total}


class RegionalClient:
    """Fixed public endpoint with a budget covering every retry attempt."""
    def __init__(self, transport, max_requests=300, retries=2, retry_delay=1):
        if not 1 <= max_requests <= 10000 or not 0 <= retries <= 5 or not 0 <= retry_delay <= 10:
            raise ValueError('Invalid request or retry bounds')
        self.transport = transport
        self.max_requests, self.retries, self.retry_delay = max_requests, retries, retry_delay
        self.used = 0
        self.attempts = []
        self.capture_root = Path(transport.out) if isinstance(transport, Client) else None

    def request_json(self, url):
        parsed = urlsplit(url)
        allowed = parsed.path == SEARCH_PATH or re.fullmatch(re.escape(DETAIL_PATH) + r'(?:[A-Za-z0-9_-]|%[0-9A-Fa-f]{2})+', parsed.path)
        if (parsed.scheme != 'https' or parsed.hostname != 'rest.arbeitsagentur.de' or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment or not allowed):
            raise ValueError('Unsupported regional endpoint')
        result = {'state': 'request_budget_exhausted', 'url': url}
        for attempt in range(self.retries + 1):
            if self.used >= self.max_requests:
                return {'state': 'request_budget_exhausted', 'url': url}, None
            self.used += 1
            # Client caches errors too. Each live attempt must be fresh and keep
            # an immutable capture rather than overwrite a previous failure.
            if isinstance(self.transport, Client):
                self.transport.cache.pop(url, None)
                self.transport.out = self.capture_root / f'attempt-{self.used:05d}'
                self.transport.out.mkdir()
            record, body = self.transport._raw(url, {'X-API-Key': 'jobboerse-jobsuche', 'Accept': 'application/json'})
            result = {**record, 'url': url, 'attempt': attempt + 1}
            payload = None
            if record.get('state') == 'ok':
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeError):
                    result['state'] = 'invalid_json'
                if payload is not None and not isinstance(payload, dict):
                    result['state'] = 'invalid_response'
                    payload = None
            self.attempts.append(result)
            if result['state'] == 'ok':
                return result, payload
            retryable = result['state'] == 'network_error' or record.get('status') in {429, 500, 502, 503, 504}
            if not retryable or attempt == self.retries:
                return result, None
            if self.used < self.max_requests:
                time.sleep(min(10, self.retry_delay * (attempt + 1)))
        return result, None


def acquire(out, *, city, radius_km=35, terms=DEFAULT_TERMS, max_pages=3, page_size=100,
            max_details=100, max_requests=300, delay=1, retries=2, client=None) -> dict:
    """Capture a fresh bounded selected search and reusable normalized snapshot."""
    terms = list(dict.fromkeys(_text(term) for term in terms if _text(term)))
    if not _text(city) or not 0 <= radius_km <= 200 or not 1 <= max_pages <= 100 or not 1 <= page_size <= 100:
        raise ValueError('City and valid radius/pagination bounds are required')
    if not 1 <= len(terms) <= 50 or not 0 <= max_details <= 10000 or not 0.1 <= delay <= 60:
        raise ValueError('Invalid search terms, details or pacing bounds')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    if client is None:
        transport = Client(out/'http', delay=delay, max_requests=max_requests,
                           origin_pacer=OriginPacer(), request_budget=RequestBudget(max_requests))
        client = RegionalClient(transport, max_requests=max_requests, retries=retries)
    manifest = {'started_at': _now(), 'source': 'arbeitsagentur', 'interface': 'observed_public_endpoint',
                'selected_search': True, 'city': city, 'radius_km': radius_km, 'terms': terms,
                'max_pages_per_term': max_pages, 'page_size': page_size, 'max_details': max_details,
                'max_requests': client.max_requests, 'searches': [], 'detail_errors': [], 'state': 'complete_selected_search'}
    summaries, term_refs = {}, []
    partial = False
    def save():
        snapshot = {'source': 'arbeitsagentur', 'selected_search': True, 'observed_at': manifest['started_at'],
                    'jobs': [normalize_job(row, manifest['started_at']) for row in summaries.values()]}
        (out/'summaries.json').write_text(json.dumps(list(summaries.values()), ensure_ascii=False, indent=2)+'\n')
        (out/'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2)+'\n')
        (out/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    for term in terms:
        refs = []
        for page in range(1, max_pages + 1):
            url = BASE_URL + '/pc/v6/jobs?' + urlencode({'wo': city, 'umkreis': radius_km, 'angebotsart': 1,
                                                         'was': term, 'page': page, 'size': page_size})
            record, payload = client.request_json(url)
            search = {'term': term, 'page': page, 'url': url, 'state': record['state'], 'status': record.get('status')}
            manifest['searches'].append(search)
            if payload is None:
                partial = True
                break
            try:
                parsed = parse_search(payload)
            except ValueError as error:
                search.update(state='invalid_response', error=str(error))
                partial = True
                break
            search.update(rows=len(parsed['rows']), total=parsed['total'], invalid_count=parsed['invalid_count'])
            partial |= parsed['invalid_count'] > 0
            for row in parsed['rows']:
                try:
                    job = normalize_job(row, record.get('checked_at'))
                except ValueError:
                    continue
                ref = job['external_id']
                refs.append(ref)
                if ref not in summaries:
                    summaries[ref] = {**row, '_checked_at': record.get('checked_at') or manifest['started_at'],
                                      '_search_url': url, '_search_terms': []}
                if term not in summaries[ref]['_search_terms']:
                    summaries[ref]['_search_terms'].append(term)
            total = parsed['total']
            ended = not parsed['rows'] or (total is not None and page * page_size >= total) or (total is None and len(parsed['rows']) < page_size)
            if ended:
                break
            if page == max_pages:
                search['truncated'] = True
                partial = True
        term_refs.append(refs)
        save()
        if client.used >= client.max_requests:
            partial = True
            break
    # Round-robin keeps a common first role family from consuming all details.
    selected, seen = [], set()
    for index in range(max((len(refs) for refs in term_refs), default=0)):
        for refs in term_refs:
            if index < len(refs) and refs[index] not in seen:
                selected.append(refs[index])
                seen.add(refs[index])
    if len(selected) > max_details:
        partial = True
    full = []
    for ref in selected[:max_details]:
        if client.used >= client.max_requests:
            manifest['detail_errors'].append({'reference': ref, 'state': 'request_budget_exhausted'})
            partial = True
            break
        code = quote(base64.b64encode(ref.encode()).decode(), safe='')
        url = BASE_URL + '/pc/v4/jobdetails/' + code
        record, detail = client.request_json(url)
        detail_ref = (detail.get('referenznummer') or detail.get('refnr')) if detail else None
        if detail and detail_ref == ref:
            summaries[ref] = {**summaries[ref], **detail, '_detail_url': url,
                              '_checked_at': record.get('checked_at') or _now(), '_detail_state': 'ok'}
            full.append(summaries[ref])
        else:
            state = 'identity_mismatch' if detail is not None else record['state']
            summaries[ref]['_detail_state'] = state
            manifest['detail_errors'].append({'reference': ref, 'url': url, 'state': state, 'status': record.get('status')})
            partial = True
        save()
    manifest.update(finished_at=_now(), requests_used=client.used, attempts=client.attempts,
                    unique_summaries=len(summaries), detail_count=len(full), state='partial' if partial else 'complete_selected_search')
    (out/'details.json').write_text(json.dumps(full, ensure_ascii=False, indent=2)+'\n')
    save()
    return manifest
