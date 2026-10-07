"""Recognize employer ATS tenants; read public feeds, never application APIs."""
import html
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit


def fetch_feed(client, provider, feed_url, board_url, *, conditional_headers=None, pace=None):
    """Read one feed, retaining the first response and bounded Lever scan evidence.

    ``parsed_feed`` carries aggregated jobs without rewriting the raw capture.
    Other providers retain their existing single-response parsing behavior.
    The client's public-feed allowlist, request budget and pacing still apply.
    """
    query = None
    partial = False
    if provider == 'lever':
        try:
            if not isinstance(feed_url, str) or re.search(r'[\s\x00-\x1f\x7f]', feed_url):
                raise ValueError('Whitespace or control characters in Lever URL')
            parts = urlsplit(feed_url)
            if (parts.scheme != 'https' or parts.hostname not in {'api.lever.co', 'api.eu.lever.co'}
                    or parts.username or parts.password or parts.port not in (None, 443)
                    or parts.fragment or not re.fullmatch(r'/v0/postings/[A-Za-z0-9][A-Za-z0-9_.-]*', parts.path)
                    or re.search(r'%(?![0-9a-fA-F]{2})', parts.query)):
                raise ValueError('Expected an exact-host public Lever postings URL')
            pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
            query = dict(pairs)
            if len(query) != len(pairs) or any(not key or not value for key, value in pairs):
                raise ValueError('Ambiguous or empty Lever query value')
            for key in ('limit', 'skip'):
                if key in query and not re.fullmatch(r'0|[1-9][0-9]*', query[key]):
                    raise ValueError('Invalid Lever ' + key)
            if query.get('limit') == '0' or ('mode' in query and query['mode'] != 'json'):
                raise ValueError('Invalid Lever mode or limit')
            # Preserve filters and explicitly requested partial pages. Never drop
            # a filter or equate a short filtered response with a whole board.
            partial = (query.get('mode') != 'json' or query.get('limit') != '100'
                       or query.get('skip', '0') != '0'
                       or bool(set(query) - {'mode', 'limit', 'skip'}))
        except (ValueError, TypeError) as error:
            return {'url': feed_url, 'state': 'unsupported_api', 'error': str(error)}, b''

    def request(url, headers=None):
        if pace:
            pace(url)
        if provider in {'schema_org', 'html_jobs'}:
            return client.get(url)
        return client.get_feed(url, headers) if headers else client.get_feed(url)

    metadata, body = request(feed_url, conditional_headers)
    if provider != 'lever' or metadata.get('state') != 'ok' or metadata.get('status') == 304:
        return metadata, body
    metadata = dict(metadata)
    pagination = {'complete': False, 'page_limit': 10, 'pages': [], 'error': None}
    metadata['pagination'] = pagination
    jobs, seen = [], set()
    current_url, page_meta, page_body = feed_url, metadata, body
    for page_index in range(10):
        evidence = {key: page_meta[key] for key in ('state', 'status', 'capture') if key in page_meta}
        evidence['url'] = current_url
        pagination['pages'].append(evidence)
        if page_meta.get('state') != 'ok' or page_meta.get('status') == 304:
            pagination['error'] = 'Lever continuation failed: ' + str(page_meta.get('error') or page_meta.get('state'))
            break
        try:
            parsed = parse_feed(provider, page_body, board_url)
            # parse_feed stringifies provider IDs; do not mistake a null or
            # composite value for a stable ID when deciding scan completeness.
            for row, job in zip(json.loads(page_body), parsed['jobs']):
                job_id = row.get('id')
                if (not isinstance(job_id, (str, int)) or isinstance(job_id, bool)
                        or not str(job_id).strip()):
                    raise ValueError('Expected a stable Lever job ID')
                # Validate the entire page before aggregating it. Persistence
                # requires text fields; accepting structured values here would
                # roll back even the valid jobs recovered on earlier pages.
                if not isinstance(job['title'], str) or not job['title'].strip():
                    raise ValueError('Expected a nonblank Lever job title')
                if not isinstance(job['url'], str):
                    raise ValueError('Expected a Lever job URL string')
                job_url = urlsplit(job['url'])
                if job_url.scheme not in {'http', 'https'} or not job_url.hostname:
                    raise ValueError('Expected an HTTP(S) Lever job URL with a host')
                if job.get('location') is None:
                    job['location'] = ''
                elif not isinstance(job['location'], str):
                    raise ValueError('Expected a Lever job location string or null')
        except (ValueError, TypeError) as error:
            if page_index == 0:
                # Consumers keep the existing schema-error handling for page one.
                pagination['error'] = 'Lever schema error: ' + str(error)
                metadata['schema_error'] = str(error)
                return metadata, body
            pagination['error'] = 'Lever continuation schema error: ' + str(error)
            break
        evidence['items'] = len(parsed['jobs'])
        duplicate = False
        for job in parsed['jobs']:
            if job['id'] in seen:
                duplicate = True
            else:
                seen.add(job['id'])
                jobs.append(job)
        if duplicate:
            pagination['error'] = 'Lever duplicate IDs or non-progress during offset enumeration'
            break
        if partial:
            pagination['error'] = 'Lever URL represents a partial or filtered board scan'
            break
        if len(parsed['jobs']) > 100:
            pagination['error'] = 'Lever response exceeded the requested page limit'
            break
        if len(parsed['jobs']) < 100:
            pagination['complete'] = True
            break
        if page_index == 9:
            pagination['error'] = 'Lever pagination page cap reached'
            break
        next_query = {**query, 'skip': str((page_index + 1) * 100)}
        current_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(next_query), ''))
        try:
            # Validators describe page one only; never carry them to later offsets.
            page_meta, page_body = request(current_url)
        except (OSError, ValueError, TypeError) as error:
            page_meta, page_body = {'state': 'network_error', 'error': str(error)}, b''
    metadata['parsed_feed'] = {'jobs': jobs, 'complete': pagination['complete']}
    return metadata, body


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


def _recruitee_host(host):
    """A single employer tenant label, excluding vendor infrastructure."""
    return (bool(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.recruitee\.com', host))
            and host.split('.')[0] not in {'api', 'app', 'static', 'cdn', 'www', 'assets'})


def _recruitee_url(value):
    if not isinstance(value, str) or re.search(r'[\s\x00-\x1f\x7f\\]', value):
        raise ValueError('Invalid Recruitee URL')
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.port not in (None, 443) or parts.query or parts.fragment
            or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}', parts.hostname)):
        raise ValueError('Expected an unambiguous public HTTPS Recruitee URL')
    return parts


def _recruitee_description(row):
    sections = []
    for key in ('description', 'requirements'):
        value = row.findtext(key) or ''
        value = re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>', '', value, flags=re.I | re.S)
        value = re.sub(r'<li\b[^>]*>\s*(?:<p\b[^>]*>)?', '\n- ', value, flags=re.I)
        value = re.sub(r'</?(?:p|h[1-6]|ul|ol|div|li)\b[^>]*>|<br\b[^>]*>', '\n', value, flags=re.I)
        value = html.unescape(re.sub(r'<[^>]*>', '', value))
        lines = [' '.join(line.split()) for line in value.splitlines() if line.strip()]
        if lines:
            sections.append('\n'.join(lines))
    return '\n\n'.join(sections) or None


def _parse_recruitee(body, board_url):
    board = _recruitee_url(board_url)
    if not _recruitee_host(board.hostname) or board.path not in ('', '/'):
        raise ValueError('Expected the canonical Recruitee tenant board')
    # This feed contract has no pagination metadata or XML entities.
    text = body.decode('utf-8-sig') if isinstance(body, bytes) else body
    if re.search(r'<!DOCTYPE|<!ENTITY', text, re.I):
        raise ValueError('Unexpected Recruitee XML declaration')
    root = ET.fromstring(text)
    if root.tag != 'offers' or root.attrib or (root.text or '').strip():
        raise ValueError('Expected complete Recruitee offers XML')
    fields = set('id slug title description requirements highlight location country city country_code state_code postal_code remote hybrid on_site department employment_type_code category experience_code education_code salary tags min_hours max_hours min_hours_per_week max_hours_per_week company_name careers_url apply_url mailbox_email created_at updated_at published_at close_at locations'.split())
    jobs, seen, slugs = [], set(), set()
    for row in root:
        if (row.tag != 'offer' or row.attrib or (row.text or '').strip() or (row.tail or '').strip()
                or any(node.tag not in fields or node.attrib or (node.tail or '').strip() for node in row)
                or len({node.tag for node in row}) != len(row)):
            raise ValueError('Unexpected Recruitee offer schema or pagination')
        for node in row:
            if node.tag not in {'locations', 'salary', 'tags'} and len(node):
                raise ValueError('Expected scalar Recruitee field')
            if node.tag in {'locations', 'salary', 'tags'}:
                allowed = {'locations': {'location'}, 'salary': {'min', 'max', 'currency', 'period'}, 'tags': {'tag'}}[node.tag]
                if (node.text or '').strip() or any(child.tag not in allowed or child.attrib or (child.tail or '').strip() for child in node):
                    raise ValueError('Unexpected Recruitee collection')
                for child in node:
                    if node.tag == 'locations':
                        location_fields = {'id', 'country_code', 'state_code', 'country', 'state', 'name', 'street', 'postal_code', 'city', 'note'}
                        if ((child.text or '').strip() or len({field.tag for field in child}) != len(child)
                                or any(field.tag not in location_fields or field.attrib or len(field) or (field.tail or '').strip() for field in child)):
                            raise ValueError('Unexpected Recruitee location')
                    elif len(child):
                        raise ValueError('Expected scalar Recruitee collection item')
        values = {node.tag: (node.text or '').strip() for node in row}
        job_id, slug, title = (values.get(key, '') for key in ('id', 'slug', 'title'))
        if not re.fullmatch(r'[1-9][0-9]*', job_id) or job_id in seen or slug in slugs or not title or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug):
            raise ValueError('Missing or ambiguous Recruitee job identity')
        seen.add(job_id)
        slugs.add(slug)
        careers = _recruitee_url(values.get('careers_url'))
        apply = _recruitee_url(values.get('apply_url'))
        if (careers.path != '/o/' + slug or apply.path != careers.path + '/c/new'
                or careers.hostname != apply.hostname
                or (careers.hostname.endswith('.recruitee.com') and careers.hostname != board.hostname)):
            raise ValueError('Recruitee job URLs disagree with their tenant or slug')
        for flag in ('remote', 'hybrid', 'on_site'):
            if values.get(flag, '') not in ('', 'true', 'false'):
                raise ValueError('Unexpected Recruitee work arrangement flag')
        if values.get('close_at'):
            raise ValueError('Unsupported Recruitee closing-date evidence')
        # Initiative/talent rows are feed members but are not active vacancies.
        if re.search(r'\binitiativ(?:bewerbung(?:en)?|application)\b', title, re.I) or re.fullmatch(
                r'(?:initiative|unsolicited|general|speculative|open)\s+application(?:\s*\([^)]*\))?|(?:join (?:our )?)?talent (?:pool|community|network)(?:\s*\([^)]*\))?', title, re.I):
            continue
        locations = []
        for source in [row, *row.findall('./locations/location')]:
            label, country = (source.findtext('city') or '').strip(), (source.findtext('country_code') or '').strip()
            if country and not re.fullmatch(r'[A-Z]{2}', country):
                raise ValueError('Invalid Recruitee country code')
            if label:
                location = {'label': label, **({'country_code': country} if country else {})}
                if location not in locations:
                    locations.append(location)
        arrangement = next((name for flag, name in [('hybrid', 'hybrid'), ('remote', 'remote'), ('on_site', 'onsite')]
                            if values.get(flag) == 'true'), None)
        date = values.get('published_at')
        if date:
            from datetime import datetime
            date = datetime.strptime(date, '%Y-%m-%d %H:%M:%S UTC').date().isoformat()
        job = {'id': job_id, 'title': title, 'url': 'https://' + board.hostname + '/o/' + slug,
               'location': values.get('location') or values.get('city') or ''}
        _add_metadata(job, {
            'description': _recruitee_description(row), 'locations': locations or None,
            'department': values.get('department'), 'employment_type': values.get('employment_type_code'),
            'date_posted': date, 'work_arrangement': arrangement,
            'is_remote': arrangement == 'remote' if arrangement else None,
            **{key: values.get(key) for key in ('careers_url', 'apply_url', 'company_name', 'country', 'country_code', 'remote', 'hybrid', 'on_site')},
        })
        jobs.append(job)
    return {'jobs': jobs, 'complete': True}


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
    elif host.endswith('.recruitee.com'):
        if (not _recruitee_host(host) or p.scheme not in {'http', 'https'} or p.username or p.password
                or p.port not in (None, 443) or re.search(r'[\s\x00-\x1f\x7f\\]', url)
                or not (re.fullmatch(r'/(?:l/[a-z]{2}/)?(?:o/[a-z0-9][a-z0-9-]*/?)?', p.path or '/')
                        or (p.path == '/api/feeds/offers.xml' and not p.query and not p.fragment))):
            return None
        provider, tenant, board = 'recruitee', host.split('.')[0], f'https://{host}'
        feed = board + '/api/feeds/offers.xml'
    else:
        # Discovery-only support: do not invent undocumented APIs.
        suffixes = {'myworkdayjobs.com':'workday', 'softgarden.io':'softgarden',
                    'softgarden.de':'softgarden',
                    'helixjobs.com':'helix', 'onlyfy.jobs':'onlyfy',
                    'successfactors.eu':'successfactors', 'successfactors.com':'successfactors'}
        for suffix, name in suffixes.items():
            if host.endswith('.' + suffix):
                if host.split('.')[0] in {'api','app','static','cdn','www','assets'}: continue
                if name == 'softgarden' and host in {'jhfiles.s3.softgarden.de', 'jobdb.softgarden.de'}: continue
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
            utilities = {'cdn-cgi', 'external-referrals', 'oneclick-ui'}
            if tenant.casefold() in utilities:
                if (tenant.casefold() not in {'external-referrals', 'oneclick-ui'} or len(parts) != 5
                        or parts[1] != 'company' or parts[3] != 'publication'
                        or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', parts[2])
                        or parts[2].casefold() in utilities
                        or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', parts[4])):
                    return None
                tenant = parts[2]
            provider, board = 'smartrecruiters', f'https://careers.smartrecruiters.com/{tenant}'
    if not provider:
        return None
    return {'provider':provider, 'tenant':tenant, 'board_url':board, 'feed_url':feed}


def parse_feed(provider, body, board_url):
    """Fail closed on an unexpected schema; limited Lever pages stay incomplete."""
    jobs = []
    complete = True
    try:
        if provider == 'recruitee':
            return _parse_recruitee(body, board_url)
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
