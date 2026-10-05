"""Bounded company-homepage to careers/ATS discovery."""
import heapq
import itertools
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit
from .ats import identify, parse_feed
from .pages import CAREER, clean_url, inspect_page


def discover(seed, client, max_pages=6, max_depth=3):
    home = clean_url(seed['website'], seed['website'])
    if not home:
        raise ValueError('Seed website must be HTTP(S)')
    result = {**seed, 'pages':[], 'boards':[], 'status':'unresolved'}
    queue, visited, board_keys = [], set(), set()
    counter = itertools.count()
    def enqueue(url, score, depth, parent, method):
        if url and url not in visited and depth <= max_depth:
            heapq.heappush(queue,(-score,next(counter),url,depth,parent,method))
    enqueue(home,200,0,None,'seed')
    fallbacks_added = False
    while queue and len(result['pages']) < max_pages:
        _,_,url,depth,parent,method = heapq.heappop(queue)
        if url in visited: continue
        visited.add(url)
        meta,body = client.get(url)
        final = meta.get('final_url',url)
        page = {'requested_url':url, 'url':final, 'parent':parent, 'method':method,
                'depth':depth, 'fetch_state':meta['state'], 'http_status':meta.get('status'),
                'capture':meta.get('capture'), 'redirect_chain':meta.get('redirect_chain',[])}
        result['pages'].append(page)
        if meta['state'] != 'ok':
            # Do not probe paths on a homepage that is blocked or unavailable.
            if depth == 0: break
            continue
        visited.add(final)
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
                fm,fb = client.get_feed(provider['feed_url'])
                board.update(feed_state=fm['state'],feed_http_status=fm.get('status'),capture=fm.get('capture'))
                if fm['state']=='ok':
                    try:
                        parsed = parse_feed(provider['provider'],fb,provider['board_url'])
                        board.update(jobs=parsed['jobs'],job_count=len(parsed['jobs']),
                                     complete=parsed['complete'],feed_state='parsed')
                    except ValueError as error:
                        board.update(feed_state='schema_error',error=str(error))
            else:
                enqueue(provider['board_url'],95,depth+1,final,'ats_link')
        if any(b['feed_state']=='parsed' for b in result['boards']):
            result['status']='jobs_feed_found'
            break
        # A few high-value paths per page prevent a navigation tree crawl.
        for candidate in info['candidates'][:6]:
            if identify(candidate['url']): continue  # feed/detection-only path handled above
            target_host = urlsplit(candidate['url']).hostname
            current_host = urlsplit(final).hostname.removeprefix('www.')
            related = target_host.removeprefix('www.') == current_host or target_host.endswith('.'+current_host)
            if candidate['score'] >= 70 or related:
                enqueue(candidate['url'],candidate['score'],depth+1,final,'link')
    if result['status'] != 'jobs_feed_found':
        if any(p.get('classification') in {'career_content','jobposting'} for p in result['pages']):
            result['status']='career_content_found'
        elif result['boards']:
            result['status']='ats_identified'
    result['limits']={'max_pages':max_pages,'max_depth':max_depth,'max_boards':3,
                      'page_budget_reached':len(result['pages'])>=max_pages,'remaining_queue':len(queue)}
    return result
