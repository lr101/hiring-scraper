"""Recognize employer ATS tenants; read public feeds, never application APIs."""
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit


def identify(url):
    p = urlsplit(url)
    host = (p.hostname or '').lower()
    if re.search(r'\.(js|css|png|jpg|svg|woff2?)(?:$)', p.path, re.I) and not (host == 'boards.greenhouse.io' and p.path.startswith('/embed/')):
        return None
    parts = p.path.strip('/').split('/')
    tenant = parts[0]
    provider = feed = board = None
    gh_job_id = parse_qs(p.query).get('gh_jid', [''])[0]
    if re.fullmatch(r'[0-9]+',gh_job_id or '') and re.search(r'apply|application',p.path,re.I):
        # Greenhouse-hosted application routed through the employer's own domain.
        provider = 'greenhouse'
        tenant = None
        board = f'{p.scheme}://{p.netloc}'
    elif host == 'boards-api.greenhouse.io' and len(parts) >= 4 and parts[:2] == ['v1','boards']:
        tenant = parts[2]
        if re.fullmatch(r'[\w-]+', tenant):
            provider = 'greenhouse'
            board = f'https://job-boards.greenhouse.io/{tenant}'
            feed = f'https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs'
    elif host in {'boards.greenhouse.io', 'job-boards.greenhouse.io'}:
        if tenant == 'embed':
            tenant = parse_qs(p.query).get('for', [''])[0]
        if tenant and re.fullmatch(r'[\w-]+', tenant):
            provider = 'greenhouse'
            board = f'https://job-boards.greenhouse.io/{tenant}'
            feed = f'https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs'
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
                jobs.append({'id':job_id, 'title':title,
                             'url':board_url.rstrip('/')+'/job/'+job_id,
                             'location':row.findtext('office') or ''})
        else:
            data = json.loads(body)
            rows = data if provider == 'lever' else data['jobs']
            if not isinstance(rows, list):
                raise ValueError('Expected a jobs array')
            for row in rows:
                if provider == 'greenhouse':
                    job = {'id':str(row['id']), 'title':row['title'], 'url':row['absolute_url'],
                           'location':row.get('location', {}).get('name', '')}
                elif provider == 'lever':
                    job = {'id':str(row['id']), 'title':row['text'], 'url':row['hostedUrl'],
                           'location':row.get('categories', {}).get('location', '')}
                elif provider == 'ashby':
                    if row.get('isListed') is False:
                        continue
                    job = {'id':str(row.get('id') or row['jobUrl']), 'title':row['title'],
                           'url':row['jobUrl'], 'location':row.get('location', '')}
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
