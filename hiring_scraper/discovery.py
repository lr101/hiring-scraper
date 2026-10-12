"""Bounded company-homepage to careers/ATS discovery."""
import heapq
import html
import itertools
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit, urlunsplit
from .ats import fetch_feed, identify, parse_feed
from .html_jobs import extract_html_jobs, html_job_key
from .pages import (CAREER, JOB_DETAIL_PATH, JOB_LISTING_PATH, NON_JOB_SECTION_PATH,
                    clean_url, inspect_page)

MAX_BROWSER_RENDER_PAGES = 4
BROWSER_RENDER_REQUEST_LIMIT = 16
MAX_PERSONIO_POSTING_SCOPES = 24
_GENERIC_COMPANY_WORDS = {
    'ag', 'ev', 'gmbh', 'kg', 'mbh', 'company', 'group', 'holding', 'jobs', 'career',
    'karriere', 'catering', 'service', 'services', 'solutions', 'consulting', 'systems',
    'software', 'technology', 'technologies', 'engineering', 'media', 'digital', 'deutschland',
    'germany', 'baden', 'wuerttemberg', 'badenwuerttemberg', 'karlsruhe',
}
_MULTI_LABEL_PUBLIC_SUFFIXES = {'co.uk', 'org.uk', 'ac.uk', 'com.au', 'net.au', 'org.au',
                                'co.nz', 'com.br', 'com.cn', 'co.jp', 'com.sg', 'co.in'}


def _german_transliterate(value):
    return ((value or '').replace('Ä', 'Ae').replace('Ö', 'Oe').replace('Ü', 'Ue')
            .replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss'))


def _normalized_host(url):
    return (urlsplit(url or '').hostname or '').casefold().removeprefix('www.').strip('.')


def _same_site(host, official_host):
    return bool(host and official_host and (
        host == official_host or host.endswith('.' + official_host) or official_host.endswith('.' + host)))


def _brand_tokens(name):
    tokens = []
    text = _german_transliterate(name)
    for raw in re.findall(r'[^\W_]+', unicodedata.normalize('NFKD', text), re.UNICODE):
        token = ''.join(char for char in raw.casefold() if char.isalnum())
        if not token or token in _GENERIC_COMPANY_WORDS:
            continue
        # Keep short all-caps brands such as USU, while rejecting short legal suffixes.
        if len(token) >= 4 or (len(token) >= 3 and raw.isupper()):
            tokens.append(token)
    return tokens


def _brand_text(value):
    normalized = unicodedata.normalize('NFKD', _german_transliterate(value).casefold())
    return ''.join(' ' if unicodedata.combining(char) or not char.isalnum() else char for char in normalized)


def _shared_registered_domain(host_a, host_b):
    a, b = (host_a or '').split('.'), (host_b or '').split('.')
    if len(a) < 2 or len(b) < 2:
        return False
    suffix = '.'.join(a[-2:])
    suffix_width = 3 if suffix in _MULTI_LABEL_PUBLIC_SUFFIXES else 2
    return (len(a) >= suffix_width + 1 and len(b) >= suffix_width + 1 and
            a[-suffix_width:] == b[-suffix_width:])


def _hostname_has_brand(host, tokens):
    text = _brand_text(host)
    return any(re.search(r'(?<![a-z0-9])' + re.escape(token) + r'(?![a-z0-9])', text)
               for token in tokens)


def _first_party_hosts(seed, pages):
    """Find the OSM hostname and branded aliases established by the fetched homepage."""
    hosts = {_normalized_host(seed.get('website'))} - {''}
    brand_tokens = _brand_tokens(seed.get('name', ''))
    root = next((page for page in pages if page.get('depth') == 0), None)
    if root:
        root_urls = [root.get('requested_url'), root.get('url')]
        root_urls.extend(root.get('redirect_chain') or [])
        for url in root_urls:
            host = _normalized_host(url)
            if not host:
                continue
            if (any(_same_site(host, known) or _shared_registered_domain(host, known) for known in hosts)
                    or _hostname_has_brand(host, brand_tokens)):
                hosts.add(host)
    return hosts


def _same_first_party_site(url, hosts):
    host = _normalized_host(url)
    return any(_same_site(host, known) for known in hosts)


def _needs_browser_render(url, info, body, extraction):
    """Render only career listing routes whose static response has no jobs."""
    path = urlsplit(url).path
    has_listing_route = bool(JOB_LISTING_PATH.search(path))
    has_career_route_without_links = bool(CAREER.search(path) and not any(
        candidate.get('score', 0) >= 84 for candidate in info.get('candidates', [])))
    if extraction.get('jobs') or not (has_listing_route or has_career_route_without_links):
        return False
    if (not has_listing_route and
            info.get('classification') not in {'career_content', 'jobposting'} and
            not CAREER.search(url)):
        return False
    source = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else str(body)
    return bool(re.search(r'<(?:script|iframe|form)\b|__next|__nuxt|data-hydration|job.?search',
                          source, re.I))


def _source_relationship(seed, source_page, pages):
    """Classify source provenance before deciding whether extracted rows are safe."""
    hosts = _first_party_hosts(seed, pages)
    if _same_first_party_site(source_page.get('url'), hosts):
        return 'first_party'
    brand_tokens = _brand_tokens(seed.get('name', ''))
    brand_evidence = _brand_text(' '.join((source_page.get('title') or '', source_page.get('headings') or '',
                                           source_page.get('url') or '')))
    branded = any(re.search(r'(?<![a-z0-9])' + re.escape(token) + r'(?![a-z0-9])', brand_evidence)
                  for token in brand_tokens)
    page_by_url = {page.get('url'): page for page in pages}
    parent_url = source_page.get('parent')
    visited = set()
    external_chain = [source_page]
    for distance in range(4):
        if not parent_url or parent_url in visited:
            break
        visited.add(parent_url)
        parent = page_by_url.get(parent_url)
        if not parent:
            break
        if _same_first_party_site(parent_url, hosts):
            if distance == 0 and branded:
                return 'branded_external'
            gateway = external_chain[-1]
            # Keep provenance through listing/detail pages on the exact host of
            # an explicitly linked gateway with an employer-branded hostname.
            # A tenant name in a shared portal's path/title cannot authorize
            # employerless rows elsewhere on that portal.
            gateway_host = _normalized_host(gateway.get('url'))
            if _hostname_has_brand(gateway_host, brand_tokens) and all(
                    _normalized_host(item.get('url')) == gateway_host for item in external_chain):
                return 'branded_external'
            return 'linked_external'
        external_chain.append(parent)
        parent_url = parent.get('parent')
    return 'unverified_external_source'


def _job_hiring_organization(job):
    metadata = job.get('raw_metadata') or {}
    organization = metadata.get('hiring_organization') if isinstance(metadata, dict) else None
    if isinstance(organization, dict):
        return organization.get('name') or ''
    return organization if isinstance(organization, str) else ''


def _employer_matches(seed, job):
    metadata = job.get('raw_metadata') or {}
    organization = metadata.get('hiring_organization') if isinstance(metadata, dict) else None
    employer_name = (organization.get('name') or '') if isinstance(organization, dict) else (
        organization if isinstance(organization, str) else '')
    employer_url = organization.get('url') if isinstance(organization, dict) else None
    company_host = _normalized_host(seed.get('website'))
    employer_host = _normalized_host(employer_url)
    if _same_site(company_host, employer_host):
        return True
    employer_tokens = set(_brand_tokens(employer_name))
    domain_tokens = {token for token in re.split(r'[^a-z0-9]+', company_host) if token}
    domain_tokens -= {'www', 'com', 'de', 'org', 'net', 'eu', 'info', 'co'}
    if employer_tokens & domain_tokens:
        return True
    if not employer_name:
        return False
    company_tokens = set(_brand_tokens(seed.get('name', '')))
    employer_tokens = set(_brand_tokens(employer_name))
    if company_tokens & employer_tokens:
        return True
    # Preserve exact names such as public agencies where useful words are filtered
    # from token matching, while still requiring a complete normalized name match.
    company_text = _brand_text(seed.get('name', '')).strip()
    employer_text = _brand_text(employer_name).strip()
    return bool(company_text and (company_text == employer_text or
                                  company_text in employer_text or employer_text in company_text))


def trusted_html_jobs(seed, source_page, pages, jobs):
    """Return rows whose source or Schema.org employer establishes company provenance."""
    relationship = _source_relationship(seed, source_page, pages)
    if relationship == 'unverified_external_source':
        # A nested external portal may not have a direct page-parent chain back
        # to the employer. Explicit hiringOrganization identity can still tie an
        # individual structured posting to the known company.
        identified = [job for job in jobs if _job_hiring_organization(job) and
                      _employer_matches(seed, job)]
        return (identified, 'linked_external_verified') if identified else ([], relationship)
    accepted = []
    for job in jobs:
        employer = _job_hiring_organization(job)
        matches = _employer_matches(seed, job)
        if employer and not matches:
            continue
        if relationship in {'first_party', 'branded_external'} or matches:
            accepted.append(job)
    if not accepted:
        return [], 'unverified_external_source'
    if relationship == 'linked_external':
        return accepted, 'linked_external_verified'
    return accepted, relationship


def trusted_html_source(seed, source_page, pages):
    """Require first-party hosting or an explicitly linked, company-branded microsite."""
    return _source_relationship(seed, source_page, pages) in {'first_party', 'branded_external'}


def discover(seed, client, max_pages=24, max_depth=5):
    home = clean_url(seed['website'], seed['website'])
    if not home:
        raise ValueError('Seed website must be HTTP(S)')
    seed_resolution = None
    parsed_home = urlsplit(home)
    if parsed_home.scheme == 'http':
        host = parsed_home.hostname or ''
        authority = f'[{host}]' if ':' in host and not host.startswith('[') else host
        try:
            port = parsed_home.port
        except ValueError:
            port = None
        if port not in (None, 80):
            authority += f':{port}'
        secure_home = urlunsplit(('https', authority, parsed_home.path or '/', parsed_home.query, ''))
        secure_meta, _ = client.get(secure_home)
        secure_candidates = [{'url': secure_home, 'state': secure_meta.get('state'),
                              'status': secure_meta.get('status')}]
        used_www_fallback = False
        if (host and '.' in host and ':' not in host and not host.casefold().startswith('www.')
                and secure_meta.get('state') == 'network_error'):
            www_authority = f'www.{authority}'
            www_home = urlunsplit(('https', www_authority, parsed_home.path or '/', parsed_home.query, ''))
            www_meta, _ = client.get(www_home)
            secure_candidates.append({'url': www_home, 'state': www_meta.get('state'),
                                      'status': www_meta.get('status')})
            if www_meta.get('state') == 'ok':
                secure_home, secure_meta = www_home, www_meta
                used_www_fallback = True
        # Prefer a working TLS homepage. Fall back to HTTP when TLS cannot
        # connect or the secure route is absent.
        fallback_to_http = secure_meta.get('state') == 'network_error' or (
            secure_meta.get('state') == 'http_error' and secure_meta.get('status') in {404, 410})
        if not fallback_to_http:
            home = secure_meta.get('final_url', secure_home)
        seed_resolution = {
            'input_url': seed['website'], 'https_candidates': secure_candidates,
            'https_state': secure_meta.get('state'), 'https_status': secure_meta.get('status'),
            'selected_url': home,
            'method': ('https_www_fallback' if used_www_fallback else
                       'https_preferred' if not fallback_to_http else 'http_fallback'),
        }
    result = {**seed, 'pages':[], 'boards':[], 'status':'unresolved'}
    if seed_resolution:
        result['seed_resolution'] = seed_resolution
    queue, visited, board_keys, board_sources = [], set(), set(), set()
    feed_responses, personio_source_boards = {}, {}
    personio_scope_count = 0
    personio_scope_budget_reached = False
    html_job_pages, unconfirmed_role_candidates = [], []
    duplicate_redirects_skipped = 0
    browser_render_count = 0
    counter = itertools.count()
    def enqueue(url, score, depth, parent, method):
        depth_limit = max_depth + (1 if method == 'ats_evidence' else 0)
        if url and url not in visited and depth <= depth_limit:
            heapq.heappush(queue,(-score,next(counter),url,depth,parent,method))
    enqueue(home,200,0,None,'seed')
    fallbacks_added = False
    while queue and len(result['pages']) < max_pages:
        _,_,url,depth,parent,method = heapq.heappop(queue)
        requested_key = clean_url(url,url) or url
        if requested_key in visited: continue
        visited.add(requested_key)
        meta,body = client.get(url)
        final = meta.get('final_url',url)
        final_key = clean_url(final,final) or final
        if final_key != requested_key and final_key in visited:
            duplicate_redirects_skipped += 1
            continue
        visited.add(final_key)
        page = {'requested_url':url, 'url':final, 'parent':parent, 'method':method,
                'depth':depth, 'fetch_state':meta['state'], 'http_status':meta.get('status'),
                'content_type':meta.get('content_type',''),
                'capture':meta.get('capture'), 'redirect_chain':meta.get('redirect_chain',[])}
        result['pages'].append(page)
        if meta['state'] != 'ok':
            # Do not probe paths on a homepage that is blocked or unavailable.
            if depth == 0: break
            continue
        # Only employer-owned pages establish company size; a linked shared ATS
        # may describe the platform's customers or another employer's workforce.
        if _same_first_party_site(final, _first_party_hosts(seed, result['pages'])):
            from .app.crawl_ranking import parse_employee_count
            text = re.sub(r'<[^>]+>', ' ', html.unescape(body.decode('utf-8', errors='replace')))
            size_match = re.search(r'(?<![\d.,])([1-9]\d{0,2}(?:[., ]\d{3})+|[1-9]\d*)\s*\+?\s*(?:Mitarbeit(?:er(?:innen)?|ende(?:n)?)|Beschäftigte(?:n)?|employees)\b', text, re.I)
            size_context = text[max(0, size_match.start()-160):size_match.start()] if size_match else ''
            third_party_size = bool(re.search(r'\b(?:Kunden?|customers?|clients?|Partner(?:n)?|participants?|Teilnehmer)\b', size_context, re.I))
            if size_match and not third_party_size and (employees := parse_employee_count(size_match[1])):
                result['company_size'] = {'employee_count':employees, 'source':'employer_website',
                                          'source_url':final, 'text':size_match[0]}
        if 'json' in meta.get('content_type','').lower() or urlsplit(final).path.lower().endswith('.json'):
            try:
                parsed = parse_feed('schema_org',body,final)
            except (ValueError, TypeError, json.JSONDecodeError):
                page['classification']='ordinary_page'
                continue
            page.update(classification='job_feed',jobposting_count=len(parsed['jobs']))
            supplier = next((source for source in result['pages'] if source.get('url') == parent), page)
            if _source_relationship(seed, supplier, result['pages']) not in {'first_party', 'branded_external'}:
                result.setdefault('unverified_external_ats', []).append({
                    'provider':'schema_org', 'feed_url':final, 'discovered_on':parent,
                    'rejected_job_count':len(parsed['jobs']), 'reason':'unverified_external_source'})
                continue
            origin = f'{urlsplit(final).scheme}://{urlsplit(final).netloc}'
            result['boards'].append({'provider':'schema_org','tenant':urlsplit(final).hostname,
                                     'board_url':origin,'feed_url':final,'evidence_url':url,
                                     'evidence_kind':'linked_json_feed','discovered_on':parent,
                                     'job_count':len(parsed['jobs']),'feed_state':'parsed','complete':parsed['complete'],
                                     'jobs':parsed['jobs'],'feed_http_status':meta.get('status'),'capture':meta.get('capture')})
            result['status']='jobs_feed_found'
            continue
        if depth == 0 and _same_first_party_site(final, _first_party_hosts(seed, result['pages'])):
            root_text = body.decode('utf-8', errors='replace')
            root_info = inspect_page(final, root_text, preferred_locations=seed.get('preferred_locations'))
            visible = re.sub(r'<[^>]+>', ' ', html.unescape(root_text))
            parked = re.search(r'\b(?:domain (?:is )?for sale|buy this domain|domain steht zum verkauf|domain[- ]?parking)\b', visible, re.I)
            if (parked and not root_info.get('candidates') and not root_info.get('ats') and
                    not root_info.get('jobposting_count') and root_info.get('classification') not in {'career_content','jobposting'}):
                # An explicit parked seed is a complete check of this source. There
                # is no employer career tree to probe or truncate on a parked domain.
                page.update(root_info)
                page['irrelevant_evidence'] = parked[0]
                result.update(irrelevant=True, complete=True, irrelevant_reason='Explicitly parked domain')
                queue.clear()
                break
        text = body.decode('utf-8',errors='replace')
        info = inspect_page(final,text,preferred_locations=seed.get('preferred_locations'))
        page.update(info)
        if not fallbacks_added:
            if not any(provider.get('feed_url') for provider in info.get('ats', [])):
                for path in ['/sitemap.xml','/karriere','/careers','/jobs']:
                    enqueue(urljoin(final,path), 3 if path.endswith('.xml') else 1, 1,final,'fallback')
            fallbacks_added = True
        if method == 'sitemap' or urlsplit(final).path.endswith('.xml'):
            try:
                root = ET.fromstring(body)
                locs = [e.text.strip() for e in root.iter() if e.tag.rsplit('}',1)[-1]=='loc' and e.text]
                index = root.tag.rsplit('}',1)[-1]=='sitemapindex'
                choices = [u for u in locs if CAREER.search(u)]
                if index: choices = choices or locs[:2]
                for target in choices[:4]:
                    if urlsplit(target).hostname == urlsplit(final).hostname:
                        enqueue(target,60,depth+1,final,'sitemap' if index else 'sitemap_link')
                page.update(classification='sitemap', candidate_count=len(choices))
            except ET.ParseError:
                page['classification']='invalid_sitemap'
            continue
        page['candidates'] = info['candidates'][:12]
        is_career_page = (info.get('classification') in {'career_content','jobposting'} or
                          info.get('classification') != 'placeholder' and
                          (CAREER.search(urlsplit(final).path) or
                           JOB_LISTING_PATH.search(urlsplit(final).path)))
        extraction = None
        if is_career_page:
            extraction = extract_html_jobs(body, final)
            remaining_requests = (getattr(client, 'max_requests', 0) -
                                  len(getattr(client, 'records', [])))
            if (_needs_browser_render(final, info, body, extraction) and
                    browser_render_count < MAX_BROWSER_RENDER_PAGES and
                    remaining_requests >= BROWSER_RENDER_REQUEST_LIMIT and
                    trusted_html_source(seed, page, result['pages'])):
                from .browser import render_public_page
                browser_render_count += 1
                rendered_meta, rendered_body = render_public_page(
                    client, final, request_limit=BROWSER_RENDER_REQUEST_LIMIT)
                page['browser_render_state'] = rendered_meta.get('state')
                if rendered_meta.get('error'):
                    page['browser_render_error'] = rendered_meta['error'][:500]
                if rendered_meta.get('browser_requests') is not None:
                    page['browser_requests'] = rendered_meta['browser_requests']
                if rendered_meta.get('state') == 'ok' and rendered_meta.get('rendered'):
                    rendered_info = inspect_page(
                        final, rendered_body, preferred_locations=seed.get('preferred_locations'))
                    page.update(rendered_info)
                    page['candidates'] = rendered_info['candidates'][:12]
                    page['browser_rendered'] = True
                    info = rendered_info
                    body = rendered_body
                    extraction = extract_html_jobs(body, final)
            page['html_extracted_job_count'] = len(extraction['jobs'])
            page['unconfirmed_role_count'] = len(extraction['role_candidates'])
            if extraction['jobs']:
                html_job_pages.append({**extraction,'url':final,'depth':depth,
                                       'classification':info.get('classification'),
                                       'capture':page.get('capture'),
                                       'provider':'browser_html' if page.get('browser_rendered') else 'html_jobs',
                                       'rendered':bool(page.get('browser_rendered'))})
            for candidate in extraction['role_candidates']:
                unconfirmed_role_candidates.append({**candidate,'company_name':seed['name'],
                                                     'source_page_url':final})
        direct = identify(final)
        ats = list(info['ats'])
        if direct:
            ats.insert(0,{**direct,'evidence_url':final,'evidence_kind':'redirect_or_board_page'})
        for provider in ats:
            key = (provider['provider'], provider['tenant'] if provider['provider'] == 'telekom' else provider['board_url'])
            source_key = (provider['provider'], provider.get('feed_url') or provider['board_url'])
            is_personio = provider['provider'] == 'personio'
            existing_personio_board = personio_source_boards.get(source_key) if is_personio else None
            personio_scope = (re.fullmatch(r'/job/(\d+)/?', urlsplit(provider['board_url']).path)
                              if is_personio else None)
            if key in board_keys or (existing_personio_board is None and (
                    len(result['boards']) >= 3 or
                    (source_key not in board_sources and len(board_sources) >= 3))):
                continue
            if personio_scope and personio_scope_count >= MAX_PERSONIO_POSTING_SCOPES:
                personio_scope_budget_reached = True
                continue
            if provider['feed_url']:
                relationship = _source_relationship(seed, page, result['pages'])
                if relationship not in {'first_party', 'branded_external'}:
                    result.setdefault('unverified_external_ats', []).append({
                        'provider': provider['provider'], 'board_url': provider['board_url'],
                        'feed_url': provider['feed_url'], 'discovered_on': final,
                        'reason': 'unverified_external_source'})
                    continue
            board_keys.add(key)
            board_sources.add(source_key)
            if personio_scope:
                personio_scope_count += 1
            board = existing_personio_board
            if board is None:
                board = {**provider,'discovered_on':final,'job_count':None,'feed_state':'not_supported',
                         'complete':False,'jobs':[]}
                if is_personio:
                    board['personio_posting_scopes'] = []
                    board['personio_full_board_authorized'] = not bool(personio_scope)
                    personio_source_boards[source_key] = board
                result['boards'].append(board)
            if is_personio and not personio_scope:
                # An official tenant-board link authorizes the complete XML
                # inventory. Preserve that authority in the saved refresh URL
                # even when a scoped posting link appeared first in page order.
                board.update(board_url=provider['board_url'],
                             evidence_url=provider.get('evidence_url') or board.get('evidence_url'),
                             evidence_kind=provider.get('evidence_kind') or board.get('evidence_kind'),
                             discovered_on=final,
                             personio_full_board_authorized=True)
            if personio_scope:
                scope_id = personio_scope[1]
                if scope_id not in board['personio_posting_scopes']:
                    board['personio_posting_scopes'].append(scope_id)
            if provider['feed_url']:
                cached_response = feed_responses.get(source_key)
                if cached_response is None:
                    cached_response = fetch_feed(client,provider['provider'],provider['feed_url'],provider['board_url'])
                    feed_responses[source_key] = cached_response
                fm,fb = cached_response
                board.update(feed_state=fm['state'],feed_http_status=fm.get('status'),capture=fm.get('capture'))
                if fm.get('pagination'):
                    board['pagination'] = fm['pagination']
                if fm['state']=='ok':
                    try:
                        if fm.get('schema_error'):
                            raise ValueError(fm['schema_error'])
                        parsed = fm.get('parsed_feed')
                        if parsed is None:
                            parsed = parse_feed(provider['provider'],fb,provider['board_url'])
                        if provider['provider'] == 'telekom':
                            # The group search includes unrelated legal employers.
                            # Keep the source employer and apply existing identity
                            # evidence checks before attributing rows to this seed.
                            all_jobs = parsed['jobs']
                            parsed = {**parsed, 'jobs':[job for job in all_jobs if _employer_matches(seed, job)]}
                            board['employer_rows_rejected'] = len(all_jobs) - len(parsed['jobs'])
                        if is_personio:
                            jobs_by_id = {job['id']: job for job in board['jobs']}
                            for job in parsed['jobs']:
                                jobs_by_id.setdefault(job['id'], job)
                            board.update(jobs=list(jobs_by_id.values()),
                                         job_count=len(jobs_by_id),
                                         complete=bool(board['complete'] or parsed['complete']),
                                         feed_state='parsed')
                        else:
                            board.update(jobs=parsed['jobs'],job_count=len(parsed['jobs']),
                                         complete=parsed['complete'],feed_state='parsed')
                    except ValueError as error:
                        board.update(feed_state='schema_error',error=str(error))
            else:
                evidence_url = provider.get('evidence_url') or provider['board_url']
                enqueue(evidence_url,95,depth+1,final,'ats_evidence')
        if any(b['feed_state']=='parsed' for b in result['boards']):
            result['status']='jobs_feed_found'
        # A few high-value paths per page prevent a navigation tree crawl.
        for candidate in info['candidates'][:24]:
            if candidate['score'] < 70: continue
            candidate_provider = identify(candidate['url'])
            if candidate_provider and candidate_provider['feed_url']:
                continue  # Native feeds handled above; unsupported details still need HTML.
            path = urlsplit(candidate['url']).path
            non_job_section = bool(NON_JOB_SECTION_PATH.search(path))
            score = candidate['score']
            # A company-branded career hostname outranks a group-wide jobs page
            # even when both links use generic "Jobs" labels.
            if _hostname_has_brand(_normalized_host(candidate['url']), _brand_tokens(seed.get('name', ''))):
                score += 20
            if JOB_DETAIL_PATH.search(path) and not non_job_section:
                score += 20
            elif JOB_LISTING_PATH.search(path) and not non_job_section:
                score += 8
            enqueue(candidate['url'],score,depth+1,final,'link')
    if unconfirmed_role_candidates:
        result['unconfirmed_role_candidates'] = unconfirmed_role_candidates
    pages_by_url = {page.get('url'): page for page in result['pages']}
    trusted_html_pages, unverified_html_pages = [], []
    for item in html_job_pages:
        source_page = pages_by_url.get(item['url'], {})
        safe_jobs, trust = trusted_html_jobs(seed, source_page, result['pages'], item['jobs'])
        source_page['html_extraction_trust'] = trust
        if safe_jobs:
            trusted_html_pages.append({**item, 'jobs': safe_jobs})
        rejected_count = len(item['jobs']) - len(safe_jobs)
        if rejected_count:
            unverified_html_pages.append({**item, 'rejected_job_count': rejected_count})
    if unverified_html_pages:
        result['unverified_external_html_pages'] = [
            {'url': item['url'], 'job_count': item.get('rejected_job_count', len(item['jobs'])),
             'reason': 'unverified_external_source'}
            for item in unverified_html_pages]
    seen_html_jobs = {}
    for item in trusted_html_pages:
        unique_jobs = []
        for job in item['jobs']:
            key = html_job_key(job)
            existing = seen_html_jobs.get(key)
            if existing is None:
                seen_html_jobs[key] = job
                unique_jobs.append(job)
                continue
            for field, value in job.items():
                if value in (None, '', [], {}) or field == 'id':
                    continue
                if field == 'raw_metadata':
                    existing[field] = {**existing.get(field, {}), **value}
                elif field == 'description':
                    if len(value) > len(existing.get(field) or ''):
                        existing[field] = value
                elif not existing.get(field):
                    existing[field] = value
        item['jobs'] = unique_jobs
    trusted_html_pages = [item for item in trusted_html_pages if item['jobs']]
    for item in trusted_html_pages:
        source_page = pages_by_url.get(item['url'], {})
        jobs = []
        for data in item['jobs']:
            job = dict(data)
            job['raw_metadata'] = dict(job.get('raw_metadata') or {})
            job['raw_metadata'].setdefault('source_page_url', item['url'])
            jobs.append(job)
        provider = item.get('provider') or 'html_jobs'
        result['boards'].append({
            'provider':provider,'tenant':urlsplit(item['url']).hostname,
            'board_url':item['url'],'feed_url':item['url'],
            'evidence_url':item['url'],
            'evidence_kind':'browser_rendered_html_job_page' if item.get('rendered') else 'static_html_job_page',
            'discovered_on':item['url'],'job_count':len(jobs),
            'feed_state':'parsed','complete':bool(item.get('complete')),'jobs':jobs,
            'capture':source_page.get('capture'),'rendered':bool(item.get('rendered')),
        })
        if result['status'] != 'jobs_feed_found':
            result['status'] = 'jobs_extracted'
    if result['status'] not in {'jobs_feed_found','jobs_extracted'}:
        if any(p.get('classification') in {'career_content','jobposting'} and
               trusted_html_source(seed, p, result['pages']) for p in result['pages']):
            result['status']='career_content_found'
        elif result['boards']:
            result['status']='ats_identified'
    result['limits']={'max_pages':max_pages,'max_depth':max_depth,'max_boards':3,
                      'browser_render_pages':browser_render_count,
                      'browser_render_page_limit':MAX_BROWSER_RENDER_PAGES,
                      'max_personio_posting_scopes':MAX_PERSONIO_POSTING_SCOPES,
                      'personio_scope_budget_reached':personio_scope_budget_reached,
                      'page_budget_reached':len(result['pages'])>=max_pages and not result.get('irrelevant'),'remaining_queue':len(queue),
                      'duplicate_redirects_skipped':duplicate_redirects_skipped}
    return result
