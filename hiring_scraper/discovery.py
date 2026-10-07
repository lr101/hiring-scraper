"""Bounded company-homepage to careers/ATS discovery."""
import heapq
import itertools
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit, urlunsplit
from .ats import fetch_feed, identify, parse_feed
from .html_jobs import extract_html_jobs, html_job_key
from .pages import CAREER, clean_url, inspect_page

JOB_LISTING_PATH = re.compile(r'(?:^|/)(?:jobs?|stellenangebote?|jobsuche|open-positions|alle-jobs|all-jobs)/?$', re.I)
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
            return 'linked_external'
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
        return [], relationship
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


def discover(seed, client, max_pages=6, max_depth=3):
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
        if (secure_meta.get('state') == 'robots_unavailable' and host and '.' in host
                and ':' not in host and not host.casefold().startswith('www.')):
            www_authority = f'www.{authority}'
            www_home = urlunsplit(('https', www_authority, parsed_home.path or '/', parsed_home.query, ''))
            www_meta, _ = client.get(www_home)
            secure_candidates.append({'url': www_home, 'state': www_meta.get('state'),
                                      'status': www_meta.get('status')})
            if www_meta.get('state') == 'ok':
                secure_home, secure_meta = www_home, www_meta
                used_www_fallback = True
        # Prefer a working TLS homepage. Only fall back to HTTP when TLS cannot
        # connect or the secure route is absent; robots blocks remain blocks.
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
    queue, visited, board_keys = [], set(), set()
    html_job_pages, unconfirmed_role_candidates = [], []
    duplicate_redirects_skipped = 0
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
        if 'json' in meta.get('content_type','').lower() or urlsplit(final).path.lower().endswith('.json'):
            try:
                parsed = parse_feed('schema_org',body,final)
            except (ValueError, TypeError, json.JSONDecodeError):
                page['classification']='ordinary_page'
                continue
            page.update(classification='job_feed',jobposting_count=len(parsed['jobs']))
            origin = f'{urlsplit(final).scheme}://{urlsplit(final).netloc}'
            result['boards'].append({'provider':'schema_org','tenant':urlsplit(final).hostname,
                                     'board_url':origin,'feed_url':final,'evidence_url':url,
                                     'evidence_kind':'linked_json_feed','discovered_on':parent,
                                     'job_count':len(parsed['jobs']),'feed_state':'parsed','complete':parsed['complete'],
                                     'jobs':parsed['jobs'],'feed_http_status':meta.get('status'),'capture':meta.get('capture')})
            result['status']='jobs_feed_found'
            break
        if not fallbacks_added:
            for path in ['/sitemap.xml','/karriere','/careers','/jobs']:
                enqueue(urljoin(final,path), 3 if path.endswith('.xml') else 1, 1,final,'fallback')
            fallbacks_added = True
        text = body.decode('utf-8',errors='replace')
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
        info = inspect_page(final,text)
        page.update(info)
        page['candidates'] = info['candidates'][:12]
        if info.get('classification') in {'career_content','jobposting'} or (
                info.get('classification') != 'placeholder' and CAREER.search(urlsplit(final).path)):
            extraction = extract_html_jobs(body, final)
            page['html_extracted_job_count'] = len(extraction['jobs'])
            page['unconfirmed_role_count'] = len(extraction['role_candidates'])
            if extraction['jobs']:
                html_job_pages.append({**extraction,'url':final,'depth':depth,
                                       'classification':info.get('classification'),
                                       'capture':page.get('capture')})
            for candidate in extraction['role_candidates']:
                unconfirmed_role_candidates.append({**candidate,'company_name':seed['name'],
                                                     'source_page_url':final})
        direct = identify(final)
        ats = list(info['ats'])
        if direct:
            ats.insert(0,{**direct,'evidence_url':final,'evidence_kind':'redirect_or_board_page'})
        for provider in ats:
            key = (provider['provider'],provider['board_url'])
            if key in board_keys or len(board_keys)>=3: continue
            board_keys.add(key)
            board = {**provider,'discovered_on':final,'job_count':None,'feed_state':'not_supported',
                     'complete':False,'jobs':[]}
            result['boards'].append(board)
            if provider['feed_url']:
                fm,fb = fetch_feed(client,provider['provider'],provider['feed_url'],provider['board_url'])
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
                        board.update(jobs=parsed['jobs'],job_count=len(parsed['jobs']),
                                     complete=parsed['complete'],feed_state='parsed')
                    except ValueError as error:
                        board.update(feed_state='schema_error',error=str(error))
            else:
                evidence_url = provider.get('evidence_url') or provider['board_url']
                enqueue(evidence_url,95,depth+1,final,'ats_evidence')
        if any(b['feed_state']=='parsed' for b in result['boards']):
            result['status']='jobs_feed_found'
            break
        # A few high-value paths per page prevent a navigation tree crawl.
        for candidate in info['candidates'][:12]:
            if candidate['score'] < 70: continue
            if identify(candidate['url']): continue  # feed/detection-only path handled above
            path = urlsplit(candidate['url']).path
            score = candidate['score']
            if JOB_LISTING_PATH.search(path):
                score += 20
            enqueue(candidate['url'],score,depth+1,final,'link')
    if unconfirmed_role_candidates:
        result['unconfirmed_role_candidates'] = unconfirmed_role_candidates
    has_parsed_native_feed = any(board.get('feed_state') == 'parsed' and board.get('provider') != 'html_jobs'
                                 for board in result['boards'])
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
    if trusted_html_pages and not has_parsed_native_feed:
        source_page = max(trusted_html_pages, key=lambda item:(len(item['jobs']),
                          item.get('classification') == 'career_content', -item.get('depth', 0)))
        merged_jobs = {}
        for item in trusted_html_pages:
            for job in item['jobs']:
                job = dict(job)
                job['raw_metadata'] = dict(job.get('raw_metadata') or {})
                job['raw_metadata'].setdefault('source_page_url', item['url'])
                key = html_job_key(job)
                current = merged_jobs.get(key)
                if current is None:
                    current = dict(job)
                    current['raw_metadata'] = dict(job.get('raw_metadata') or {})
                    merged_jobs[key] = current
                    continue
                old_method = (current.get('raw_metadata') or {}).get('extraction_method')
                new_method = (job.get('raw_metadata') or {}).get('extraction_method')
                if new_method == 'html_job_detail' and old_method != 'html_job_detail':
                    current['title'] = job['title']
                    current['url'] = job['url']
                    current['id'] = job.get('id')
                for field, value in job.items():
                    if value in (None, '', [], {}):
                        continue
                    if field == 'raw_metadata':
                        current[field] = {**current.get(field, {}), **value}
                    elif field == 'description' and len(value) <= len(current.get(field) or ''):
                        continue
                    elif not current.get(field) or field == 'description':
                        current[field] = value
        jobs = sorted(merged_jobs.values(), key=lambda job:job['title'].casefold())
        result['boards'].append({
            'provider':'html_jobs','tenant':urlsplit(source_page['url']).hostname,
            'board_url':source_page['url'],'feed_url':source_page['url'],
            'evidence_url':source_page['url'],'evidence_kind':'static_html_job_page',
            'discovered_on':source_page['url'],'job_count':len(jobs),
            'feed_state':'parsed','complete':source_page['complete'],'jobs':jobs,
            'capture':source_page.get('capture'),
        })
        if result['status'] != 'jobs_feed_found':
            result['status'] = 'jobs_extracted'
    if result['status'] not in {'jobs_feed_found','jobs_extracted'}:
        if any(p.get('classification') in {'career_content','jobposting'} and
               p.get('html_extraction_trust') != 'unverified_external_source' for p in result['pages']):
            result['status']='career_content_found'
        elif result['boards']:
            result['status']='ats_identified'
    result['limits']={'max_pages':max_pages,'max_depth':max_depth,'max_boards':3,
                      'page_budget_reached':len(result['pages'])>=max_pages,'remaining_queue':len(queue),
                      'duplicate_redirects_skipped':duplicate_redirects_skipped}
    return result
