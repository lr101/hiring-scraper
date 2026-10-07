"""Recognize employer ATS tenants; read public feeds, never application APIs."""
import html
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit


def _plain_text(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = re.sub(r"<[^>]*>", " ", html.unescape(value))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _arrangement(value):
    text = " ".join(str(value or "").strip().casefold().split())
    if "hybrid" in text:
        return "hybrid"
    if any(marker in text for marker in ("remote", "work from home", "telecommute")) and not re.search(r"remote (?:options?|available)", text):
        return "remote"
    if any(marker in text for marker in ("on-site", "onsite", "on site", "office-based", "vor ort")):
        return "onsite"
    return None


def _add_metadata(job, values):
    metadata = {}
    for key, value in values.items():
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        elif value is None or value == [] or value == {}:
            continue
        job[key] = value
        if key not in {"description", "work_arrangement", "is_remote", "employment_type", "schedule",
                       "department", "seniority", "date_posted", "salary", "locations"}:
            metadata[key] = value
    if metadata:
        job["raw_metadata"] = metadata


def _personio_description(row):
    parts = []
    for node in row.findall("./jobDescriptions/jobDescription"):
        # Personio XML commonly carries HTML as escaped text inside each section.
        section = " ".join("".join(node.itertext()).split())
        cleaned = _plain_text(section)
        if cleaned:
            parts.append(cleaned)
    return "\n\n".join(parts) or None


def _iso_date(value):
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        return value[:10] if value.strip() else None
    return None


def identify(url):
    p = urlsplit(url)
    host = (p.hostname or '').lower()
    greenhouse_embeds = {'boards.greenhouse.io', 'boards.eu.greenhouse.io'}
    if re.search(r'\.(js|css|png|jpg|svg|woff2?)(?:$)', p.path, re.I) and not (host in greenhouse_embeds and p.path.startswith('/embed/')):
        return None
    parts = p.path.strip('/').split('/')
    tenant = parts[0]
    provider = feed = board = None
    gh_job_id = parse_qs(p.query).get('gh_jid', [''])[0]
    if re.fullmatch(r'[0-9]+',gh_job_id or '') and re.search(r'apply|application|bewerbung',p.path,re.I):
        # Greenhouse-hosted application routed through the employer's own domain.
        provider = 'greenhouse'
        tenant = None
        board = f'{p.scheme}://{p.netloc}'
    elif host == 'boards-api.greenhouse.io' and len(parts) >= 4 and parts[:2] == ['v1','boards']:
        tenant = parts[2]
        if re.fullmatch(r'[\w-]+', tenant):
            provider = 'greenhouse'
            board = f'https://job-boards.greenhouse.io/{tenant}'
            feed = f'https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs?content=true'
    elif host in greenhouse_embeds | {'job-boards.greenhouse.io', 'job-boards.eu.greenhouse.io'}:
        if tenant == 'embed':
            tenant = parse_qs(p.query).get('for', [''])[0]
        if tenant and re.fullmatch(r'[\w-]+', tenant):
            provider = 'greenhouse'
            board_host = 'job-boards.eu.greenhouse.io' if host == 'job-boards.eu.greenhouse.io' else 'job-boards.greenhouse.io'
            board = f'https://{board_host}/{tenant}'
            feed = f'https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs?content=true'
    elif host in {'jobs.lever.co', 'jobs.eu.lever.co'} and re.fullmatch(r'[\w-]+', tenant):
        provider = 'lever'
        region = 'eu.' if host == 'jobs.eu.lever.co' else ''
        board = f'https://jobs.{region}lever.co/{tenant}'
        feed = f'https://api.{region}lever.co/v0/postings/{tenant}?mode=json&limit=100'
    elif re.fullmatch(r'[\w-]+\.jobs\.personio\.(de|com)', host):
        provider = 'personio'
        tenant = host.split('.')[0]
        board = f'https://{host}'
        feed = board + '/xml'
    elif host == 'jobs.ashbyhq.com' and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', tenant):
        provider = 'ashby'
        board = f'https://{host}/{tenant}'
        feed = f'https://api.ashbyhq.com/posting-api/job-board/{tenant}'
    else:
        # Discovery-only support: do not invent undocumented APIs.
        suffixes = {'myworkdayjobs.com':'workday', 'softgarden.io':'softgarden',
                    'softgarden.de':'softgarden', 'recruitee.com':'recruitee',
                    'helixjobs.com':'helix', 'onlyfy.jobs':'onlyfy',
                    'successfactors.eu':'successfactors', 'successfactors.com':'successfactors'}
        for suffix, name in suffixes.items():
            if host.endswith('.' + suffix):
                if host.split('.')[0] in {'api','app','static','cdn','www','assets'}: continue
                if name == 'softgarden' and (host.split('.')[0] == 'certificate' or re.search(r'/(?:imprint|impressum|data-security|privacy|datenschutz|agb|terms?)(?:/|$)',p.path,re.I)): continue
                if name == 'workday' and (not re.fullmatch(r'[\w-]+\.wd[0-9]+\.myworkdayjobs\.com', host) or p.path.startswith('/wday/') or not p.path.strip('/')): continue
                if name == 'successfactors':
                    tenant = parse_qs(p.query).get('company', [''])[0]
                    if not tenant or not re.search(r'career',p.path,re.I): continue
                else:
                    tenant = host.split('.')[0]
                provider, board = name, url
                break
        if host in {'jobs.smartrecruiters.com', 'careers.smartrecruiters.com'} and tenant:
            provider, board = 'smartrecruiters', f'https://careers.smartrecruiters.com/{tenant}'
    if not provider:
        return None
    return {'provider':provider, 'tenant':tenant, 'board_url':board, 'feed_url':feed}


def parse_feed(provider, body, board_url):
    """Fail closed on an unexpected schema; limited Lever pages stay incomplete."""
    jobs = []
    complete = True
    try:
        if provider == 'html_jobs':
            from .html_jobs import extract_html_jobs
            parsed = extract_html_jobs(body, board_url)
            return {'jobs':parsed['jobs'],'complete':parsed['complete']}
        if provider == 'personio':
            root = ET.fromstring(body)
            if root.tag != 'workzag-jobs':
                raise ValueError('Expected Personio workzag-jobs XML')
            for row in root.findall('position'):
                job_id, title = row.findtext('id'), row.findtext('name')
                if not job_id or not title:
                    raise ValueError('Missing Personio job ID or name')
                location = row.findtext('office') or ''
                job = {'id':job_id, 'title':title,
                       'url':board_url.rstrip('/')+'/job/'+job_id,
                       'location':location}
                arrangement = _arrangement(location)
                from .html_jobs import _country_code
                country = _country_code(row.findtext('country')) or _country_code(re.sub(r'\s*\([^)]*\)\s*$', '', location))
                _add_metadata(job, {
                    'department': row.findtext('department'),
                    'employment_type': row.findtext('employmentType') or row.findtext('recruitingCategory'),
                    'schedule': row.findtext('schedule'),
                    'seniority': row.findtext('seniority'),
                    'date_posted': _iso_date(row.findtext('createdAt')),
                    'description': _personio_description(row),
                    'work_arrangement': arrangement or ('onsite' if location else None),
                    'is_remote': arrangement == 'remote' if arrangement else None,
                    'keywords': row.findtext('keywords'),
                    'office': location or None,
                    'country': row.findtext('country'),
                    'locations': [{'label': location, 'country_code': country}] if location and country else None,
                    'occupation': row.findtext('occupation'),
                    'recruiting_category': row.findtext('recruitingCategory'),
                })
                jobs.append(job)
        else:
            data = json.loads(body)
            if provider == 'schema_org':
                if data.get('@type') != 'DataFeed' or not isinstance(data.get('dataFeedElement'), (list, dict)):
                    raise ValueError('Expected schema.org DataFeed with dataFeedElement')
                rows = data['dataFeedElement']
                if isinstance(rows, dict): rows = [rows]
                total = data.get('numberOfItems', len(rows))
                complete = total == len(rows)
                for entry in rows:
                    posting = entry.get('item') if isinstance(entry, dict) and entry.get('@type') == 'DataFeedItem' else entry
                    if not isinstance(posting, dict) or posting.get('@type') != 'JobPosting':
                        raise ValueError('Expected JobPosting in DataFeed')
                    identifier = posting.get('identifier')
                    job_id = identifier.get('value') if isinstance(identifier, dict) else identifier
                    from .html_jobs import _schema_locations
                    source_locations = _schema_locations(posting.get('jobLocation'))
                    locations = [item['label'] for item in source_locations]
                    job = {'id':str(job_id or posting.get('url','')), 'title':posting.get('title'),
                           'url':posting.get('url'), 'location':'; '.join(locations)}
                    _add_metadata(job, {
                        'date_posted': _iso_date(posting.get('datePosted')),
                        'employment_type': ', '.join(item for item in posting['employmentType'] if isinstance(item, str)) if isinstance(posting.get('employmentType'), list) else _plain_text(posting.get('employmentType')),
                        'description': _plain_text(posting.get('description')),
                        'work_arrangement': 'remote' if posting.get('jobLocationType') == 'TELECOMMUTE' else None,
                        'is_remote': True if posting.get('jobLocationType') == 'TELECOMMUTE' else None,
                        'salary': json.dumps(posting.get('baseSalary'), ensure_ascii=False) if posting.get('baseSalary') else None,
                        'job_location_type': posting.get('jobLocationType'),
                        'validThrough': posting.get('validThrough'),
                        'valid_through': posting.get('validThrough'),
                        'applicantLocationRequirements': posting.get('applicantLocationRequirements'),
                        **{key: posting[key] for key in ('skills', 'qualifications', 'experienceRequirements', 'educationRequirements') if key in posting},
                    })
                    if source_locations:
                        job['locations'] = source_locations
                    if not job['title'] or not job['id'] or not job['url'] or urlsplit(job['url']).scheme not in {'http', 'https'}:
                        raise ValueError('Invalid schema.org JobPosting')
                    jobs.append(job)
                return {'jobs':jobs,'complete':complete}
            rows = data if provider == 'lever' else data['jobs']
            if not isinstance(rows, list):
                raise ValueError('Expected a jobs array')
            for row in rows:
                if provider == 'greenhouse':
                    job = {'id':str(row['id']), 'title':row['title'], 'url':row['absolute_url'],
                           'location':(row.get('location') or {}).get('name', '')}
                    departments = row.get('departments') or []
                    offices = row.get('offices') or []
                    locations = []
                    seen_locations = set()
                    for office in offices:
                        name = office.get('name') if isinstance(office, dict) else None
                        if name and name.strip().casefold() not in seen_locations:
                            seen_locations.add(name.strip().casefold())
                            from .html_jobs import _country_code
                            country = _country_code(office.get('country_code') or office.get('country')) or _country_code(re.sub(r'\s*\([^)]*\)\s*$', '', name))
                            locations.append({'label':name.strip(), **({'country_code': country} if country else {})})
                    # Some Greenhouse boards put a full street address in `location.name`
                    # while their office label is only a legal entity. Preserve the
                    # original text, and expose a German postcode locality separately
                    # so city filters can match it.
                    address = (row.get('location') or {}).get('name') or ''
                    address_localities = re.findall(r'\b\d{5}\s+([^,;]+)', address)
                    for locality in address_localities:
                        locality = locality.strip()
                        if locality and locality.casefold() not in seen_locations:
                            seen_locations.add(locality.casefold())
                            locations.append({'label':locality})
                    # Greenhouse may return a city in location.name and only the
                    # employer's legal entity in offices[].name. Keep that primary
                    # city label so seed import and refresh workers can geocode it.
                    normalized_address = address.strip().casefold()
                    office_labels = [
                        item['label'].casefold() for item in locations
                        if isinstance(item, dict) and item.get('label')
                    ]
                    address_is_an_office_label = any(
                        normalized_address == label
                        or normalized_address.startswith(label + ',')
                        or normalized_address.startswith(label + ' -')
                        or label.startswith(normalized_address + ' -')
                        for label in office_labels
                    )
                    if address.strip() and not address_localities and not address_is_an_office_label:
                        if normalized_address not in seen_locations:
                            seen_locations.add(normalized_address)
                            locations.append({'label':address.strip()})
                    from .html_jobs import _country_code
                    remote_scope = []
                    for source_location in address.split(';'):
                        if _arrangement(source_location) != 'remote':
                            continue
                        code = _country_code(re.sub(r'\s*\([^)]*\)\s*$', '', source_location).strip())
                        if code and code not in remote_scope:
                            remote_scope.append(code)
                    # A localized posting does not inherit remote permission from
                    # another employer office. Explicit primary remote scope wins.
                    arrangement = (_arrangement(address) if address.strip() else
                                   _arrangement(' '.join(item.get('name','') for item in offices if isinstance(item, dict))))
                    posting_meta = row.get('metadata') or []
                    meta_map = {str(item.get('name','')).casefold(): item.get('value')
                                for item in posting_meta if isinstance(item, dict)}
                    _add_metadata(job, {
                        'department': ', '.join(item.get('name','') for item in departments if isinstance(item, dict) and item.get('name')),
                        'work_arrangement': arrangement,
                        'is_remote': True if arrangement == 'remote' else None,
                        'description': _plain_text(row.get('content')),
                        'salary': next((str(value) for key,value in meta_map.items() if 'salary' in key or 'compensation' in key), None),
                        'updated_at': row.get('updated_at'),
                        'requisition_id': str(row['requisition_id']) if row.get('requisition_id') is not None else None,
                        'locations': locations or None,
                        'offices': offices,
                        'remote_country_codes': remote_scope or None,
                        'remote_scope_source': 'greenhouse_location' if remote_scope else None,
                    })
                elif provider == 'lever':
                    categories = row.get('categories') or {}
                    job = {'id':str(row['id']), 'title':row['text'], 'url':row['hostedUrl'],
                           'location':categories.get('location', '')}
                    arrangement = _arrangement(row.get('workplaceType'))
                    salary_range = row.get('salaryRange')
                    _add_metadata(job, {
                        'department': categories.get('department') or categories.get('team'),
                        'employment_type': categories.get('commitment'),
                        'work_arrangement': arrangement,
                        'is_remote': True if arrangement == 'remote' else None,
                        'description': _plain_text(row.get('descriptionPlain') or row.get('description')),
                        'date_posted': _iso_date(row.get('createdAt')),
                        'salary': json.dumps(salary_range, ensure_ascii=False) if salary_range else None,
                        'workplace_type': row.get('workplaceType'),
                        'categories': categories,
                    })
                elif provider == 'ashby':
                    if row.get('isListed') is False:
                        continue
                    job = {'id':str(row.get('id') or row['jobUrl']), 'title':row['title'],
                           'url':row['jobUrl'], 'location':row.get('location', '')}
                    arrangement = _arrangement(row.get('workplaceType'))
                    if row.get('isRemote') is True:
                        arrangement = 'remote'
                    secondary_locations = row.get('secondaryLocations') or []
                    normalized_locations = []
                    for item in secondary_locations:
                        if isinstance(item, dict) and item.get('location'):
                            normalized_locations.append({'label': item['location']})
                    _add_metadata(job, {
                        'department': row.get('departmentName') or row.get('teamName'),
                        'employment_type': row.get('employmentType'),
                        'work_arrangement': arrangement,
                        'is_remote': True if arrangement == 'remote' else None,
                        'description': _plain_text(row.get('descriptionPlain') or row.get('description')),
                        'date_posted': _iso_date(row.get('publishedAt')),
                        'salary': row.get('compensationTierSummary'),
                        'locations': normalized_locations if normalized_locations else None,
                        'workplace_type': row.get('workplaceType'),
                    })
                else:
                    raise ValueError('Unsupported feed provider')
                if not job['title'] or not job['id'] or urlsplit(job['url']).scheme not in {'http', 'https'}:
                    raise ValueError('Invalid job record')
                jobs.append(job)
            if provider == 'lever':
                complete = len(rows) < 100
            if provider == 'greenhouse':
                complete = data.get('meta', {}).get('total', len(rows)) == len(rows)
        return {'jobs':jobs, 'complete':complete}
    except (KeyError, TypeError, AttributeError, ET.ParseError) as error:
        raise ValueError(f'Unexpected {provider} schema: {error}') from error
