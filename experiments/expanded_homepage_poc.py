"""Reproducible bounded comparison of homepage discovery hypotheses.

Experimental only: does not write to the app database or alter production policy.
API caches contain queries and public URL/title leads, never credentials or bodies.
"""
from __future__ import annotations
import argparse
import copy
import csv
import hashlib
import json
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from dotenv import dotenv_values
from hiring_scraper.geography import haversine_m
from hiring_scraper.http import Client, NoRedirect, OriginPacer, RequestBudget
from hiring_scraper.osm_websites import resolve_osm_websites, select_website_enrichments
from hiring_scraper.pages import Document, clean_url
from hiring_scraper.website_discovery import (
    GENERIC_NAME, PARKED, CONTACT, discover_missing_websites, host, phrase,
    public_url, root, tokens, verify_lead, _pace_search,
)

ROOT = Path('data/website-discovery/expanded')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


class SearchLab:
    def __init__(self, limit=250):
        self.key = os.getenv('TAVILY_API_KEY') or dotenv_values('.env').get('TAVILY_API_KEY')
        self.path = ROOT/'search-cache.json'
        self.cache = json.loads(self.path.read_text()) if self.path.exists() else {}
        old = json.loads(Path('reports/tavily-website-discovery-poc-results.json').read_text())
        for q in old['queries']:
            self.cache.setdefault('basic:'+q['query'], {'query':q['query'], 'depth':'basic',
                                                       'results':q['results'], 'credits':1, 'prior_run':True})
        self.lock = threading.Lock()
        self.limit, self.reserved, self.spent, self.reused = limit, 0, 0, 0
        self.available_queries, self.unavailable_queries = 0, 0
        self.disabled = False
        self.opener = build_opener(NoRedirect)

    def search(self, query, depth='basic'):
        key = depth+':'+query
        with self.lock:
            if key in self.cache:
                self.reused += 1
                self.available_queries += 1
                return self.cache[key]['results']
            cost = 2 if depth=='advanced' else 1
            if self.disabled or self.reserved + cost > self.limit:
                self.unavailable_queries += 1
                return []
            if not self.key: raise ValueError('Configure TAVILY_API_KEY for new live queries')
            self.reserved += cost
        _pace_search(1)
        request = Request('https://api.tavily.com/search', method='POST',
                          data=json.dumps({'query':query[:600], 'topic':'general', 'country':'germany',
                                           'search_depth':depth, 'auto_parameters':False, 'max_results':5,
                                           'include_answer':False, 'include_raw_content':False,
                                           'include_images':False, 'include_usage':True}).encode(),
                          headers={'Authorization':'Bearer '+self.key, 'Content-Type':'application/json'})
        try:
            with self.opener.open(request, timeout=15) as response: payload=json.loads(response.read(1_000_000))
            hits=[{'url':r['url'], 'title':r.get('title','')} for r in payload.get('results',[])
                  if isinstance(r,dict) and isinstance(r.get('url'),str)][:5]
            credits=int((payload.get('usage') or {}).get('credits',cost))
            with self.lock:
                self.spent += credits
                self.available_queries += 1
                self.cache[key]={'query':query,'depth':depth,'results':hits,'credits':credits,'prior_run':False}
                save(self.path,self.cache)
            return hits
        except Exception:
            with self.lock:
                self.disabled=True
                self.unavailable_queries += 1
            print('Search provider unavailable; stopping new API requests', flush=True)
            return []


class CrawlLab:
    def __init__(self, offline=False, http_limit=1800):
        self.directory=ROOT/'http'; self.directory.mkdir(exist_ok=True)
        for previous in ('data/website-discovery/http','data/website-discovery/tavily-http'):
            for metadata in Path(previous).glob('*.json'):
                for source in (metadata,metadata.with_suffix('.body')):
                    target=self.directory/source.name
                    if source.exists() and not target.exists(): shutil.copyfile(source,target)
        self.budget=RequestBudget(http_limit);self.pacer=OriginPacer();self.local=threading.local();self.offline=offline

    def client(self):
        if not hasattr(self.local,'client'):
            self.local.client=Client(self.directory, timeout=5, delay=1, max_requests=10000,
                                     cache_from=self.directory, request_budget=self.budget,
                                     origin_pacer=self.pacer, offline_only=self.offline)
        return self.local.client


def normalized(value):
    # Test German spelling variants and joined brand domains without fuzzy edit distance.
    return phrase(value).replace('ae','a').replace('oe','o').replace('ue','u')


def extended_identity(row, docs):
    text=' '.join(' '.join(d.text) for _,d in docs);visible=normalized(text)
    if PARKED.search(text): return None
    primary=re.split(r'\s+[-–—]\s+',row['name'],maxsplit=1)[0]
    name=normalized(primary);name_tokens=set(name.split())-{'gmbh','ag','kg','co','mbh','ug','gbr','eg','ev'}
    if not name_tokens or not name_tokens<=set(visible.split()): return None
    legal=bool(set(phrase(primary).split()) & {'gmbh','ag','kg','ug','gbr'})
    if legal and (' '+name+' ') not in (' '+visible+' '): return None
    headings=normalized(' '.join(' '.join(d.title+d.h1) for _,d in docs))
    distinctive=name_tokens-GENERIC_NAME
    if not distinctive: return None
    tags=row['tags'];city=normalized(str(tags.get('addr:city') or ''))
    postcode=str(tags.get('addr:postcode') or '')
    street=normalized(str(tags.get('addr:street') or ''))
    address=bool((postcode and postcode in visible.split()) or (street and street in visible))
    local=bool(city and city in visible)
    phone=re.sub(r'\D','',str(tags.get('phone') or tags.get('contact:phone') or ''))
    phone_match=bool(len(phone)>=8 and phone[-8:] in re.sub(r'\D','',text))
    emails=[str(tags.get(k) or '').casefold() for k in ('email','contact:email')]
    email_match=any(e and e in text.casefold() for e in emails)
    if not ((local and (address or len(distinctive)>1)) or phone_match or email_match): return None
    for url,doc in docs:
        compact_host=re.sub(r'[^a-z0-9]','',normalized(host(url)))
        brand=any(len(t)>=4 and t in compact_host for t in distinctive)
        heading=bool(distinctive & set(headings.split()))
        if brand and heading: return {'website_url':root(url),'evidence_url':url,'reason':'combined_identity'}
    return None


def extended_verify(row,url,client):
    url=public_url(url)
    if not url: return None
    pending=[url];docs=[];visited=set()
    # Root carries the brand identity when the search hit is a contact/imprint page.
    if root(url)!=url: pending.append(root(url))
    for _ in range(4):
        if not pending: break
        target=pending.pop(0)
        if target in visited: continue
        visited.add(target)
        record,body=client.get(target);final=public_url(record.get('final_url') or target)
        if record.get('state')!='ok' or not final or 'html' not in record.get('content_type','').casefold(): continue
        if docs and host(final)!=host(docs[0][0]): continue
        doc=Document();doc.feed(body.decode('utf8',errors='replace'));docs.append((final,doc))
        accepted=extended_identity(row,docs)
        if accepted: return {**accepted,'source_id':row['source_id'],'lead_url':url,'method':'extended_verification'}
        contacts=[]
        for href,label in doc.links:
            contact=public_url(clean_url(final,href))
            if contact and host(contact)==host(final) and CONTACT.search(label+' '+urlsplit(contact).path):
                score=0 if re.search('impressum|imprint',label+' '+contact,re.I) else 1 if re.search('kontakt|contact',label+' '+contact,re.I) else 2
                contacts.append((score,contact))
        for _,contact in sorted(contacts):
            if contact not in pending and contact not in visited: pending.append(contact)
        pending=pending[:4]
    return None


def choose(verified):
    by_host={host(x['website_url']):x for x in verified if x}
    return next(iter(by_host.values())) if len(by_host)==1 else None


def parallel(rows,fn):
    with ThreadPoolExecutor(max_workers=4) as pool: return list(pool.map(fn,rows))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--credit-limit',type=int,default=250)
    parser.add_argument('--http-limit',type=int,default=1800)
    parser.add_argument('--output',default='completed-results.json')
    args=parser.parse_args()
    rows=json.loads((ROOT/'cohort.json').read_text()); by_id={r['source_id']:r for r in rows}
    search=SearchLab(0 if args.offline else args.credit_limit);crawl=CrawlLab(args.offline,args.http_limit)
    leads={r['source_id']:[] for r in rows}; stages={};details={};counts={}
    previous_queries=(0,0)
    def payload(complete=False):
        return {'complete':complete,'counts':counts,'stages':stages,'details':details,
                'offline':args.offline, 'http_limit':args.http_limit,
                'historical_new_search_credits':sum(v['credits'] for v in search.cache.values() if not v['prior_run']),
                'historical_prior_search_credits':sum(v['credits'] for v in search.cache.values() if v['prior_run']),
                'search_credits_spent_this_run':search.spent,'search_cache_hits':search.reused,
                'live_website_requests':crawl.budget.used,
                'http_budget_reached':crawl.budget.used>=crawl.budget.limit}
    def record(stage,results,eligible_count):
        nonlocal previous_queries
        stage_results={r['source_id']:r for r in results if r}
        stages[stage]=stage_results
        union={sid:r for method in stages.values() for sid,r in method.items()}
        counts[stage]={'incremental_resolved_raw':len(stage_results),'eligible_count':eligible_count,'cumulative_resolved_raw':len(union),
                       'sample_resolved':sum(by_id[sid]['group']=='sample' for sid in union),
                       'diagnostic_resolved':sum(by_id[sid]['group']=='diagnostic' for sid in union),
                       'credits_spent_this_run':search.spent,'live_website_requests':crawl.budget.used,
                       'censored_by_http_cap':crawl.budget.used>=crawl.budget.limit,
                       'search_queries_available':search.available_queries-previous_queries[0],
                       'search_queries_unavailable':search.unavailable_queries-previous_queries[1]}
        previous_queries=(search.available_queries,search.unavailable_queries)
        save(ROOT/('checkpoint-'+args.output),payload())
        print(stage, json.dumps(counts[stage]),flush=True)
        return union
    print('Starting expanded benchmark:',len(rows),'companies',flush=True)
    # Existing map/Wikidata baseline, with the same saved entity cache as production research.
    entities=json.loads(Path('data/location-sources/run-2026-10-05/wikidata-osm-website-entities.json').read_text())
    entities=entities.get('entities',entities)
    osm={'elements':[{'type':r['osm_type'],'id':r['osm_id'],'tags':r['tags']} for r in rows]}
    suggestions=resolve_osm_websites([{**r,'website':None} for r in rows],osm,entities)
    selected=select_website_enrichments(suggestions)
    mapped=record('mapped_wikidata',[{'source_id':sid,'website_url':r['website_url'],
                                    'evidence_url':r['evidence_url'],'method':r['method']} for sid,r in selected.items()],len(rows))
    for sid,item in mapped.items(): by_id[sid]['website_url']=item['website_url']
    def baseline(row):
        item=copy.deepcopy(row)
        def query(q):
            hits=search.search(q);leads[row['source_id']].extend(h['url'] for h in hits)
            return hits
        decisions=discover_missing_websites([item],crawl.client(),search=query,max_searches=1)
        details[row['source_id']]={'baseline_decisions':decisions}
        if item.get('website_url'):
            return {k:item.get(k) for k in ('source_id','name','website_url','domain_evidence_url','domain_match_method')}
    union=record('production_baseline',parallel([r for r in rows if r['source_id'] not in mapped],baseline),len(rows)-len(mapped))
    # Exact normalized name reuse from already mapped firms; no guessed-name fuzzy associations.
    existing=list(csv.DictReader(open('fixtures/karlsruhe-osm-candidates.csv')))
    websites={}
    for peer in existing:
        if peer['website']: websites.setdefault(phrase(peer['name']),[]).append(peer['website'])
    def reuse(row):
        candidates=websites.get(phrase(row['name']),[])
        leads[row['source_id']].extend(candidates)
        return choose([extended_verify(row,url,crawl.client()) for url in list(dict.fromkeys(candidates))[:3]])
    union=record('exact_name_existing_map',parallel([r for r in rows if r['source_id'] not in union],reuse),len(rows)-len(union))
    # Same search hits, better page coverage and same-host evidence aggregation: zero extra API cost.
    def extended(row):
        candidates=list(dict.fromkeys(leads[row['source_id']]))
        # Keep same three eligible hosts as the production verifier for the initial ablation.
        urls=[];seen=set()
        for u in candidates:
            u=public_url(u)
            if u and host(u) not in seen:
                seen.add(host(u));urls.append(u)
        return choose([extended_verify(row,u,crawl.client()) for u in urls[:3]])
    union=record('combined_pages_and_brand_normalization',parallel([r for r in rows if r['source_id'] not in union],extended),len(rows)-len(union))
    # Simpler legal-form-free query plus regional city hint for entries lacking address tags.
    def rewrite(row):
        primary=re.split(r'\s+[-–—]\s+',row['name'],maxsplit=1)[0]
        name=re.sub(r'\b(?:GmbH|AG|KG|mbH|GbR|UG|haftungsbeschränkt)\b','',primary,flags=re.I)
        locality=row['tags'].get('addr:city') or 'Karlsruhe Region'
        q=f'{name.strip()} {locality} Kontakt Impressum'
        hits=search.search(q);leads[row['source_id']].extend(h['url'] for h in hits)
        eligible=[h for h in hits if public_url(h['url'])][:3]
        verified=[extended_verify(row,h['url'],crawl.client()) for h in eligible]
        return choose(verified)
    union=record('simplified_query_and_regional_hint',parallel([r for r in rows if r['source_id'] not in union],rewrite),len(rows)-len(union))
    # Outbound links on profile pages become leads; the profile itself can never be the homepage.
    def outbound(row):
        urls=list(dict.fromkeys(leads[row['source_id']]))
        checked=0;targets=[]
        for url in urls:
            if checked>=2: break
            safe=clean_url(url,url)
            if not safe or public_url(safe): continue
            checked+=1;record,body=crawl.client().get(safe)
            if record.get('state')!='ok' or 'html' not in record.get('content_type',''): continue
            doc=Document();doc.feed(body.decode('utf8',errors='replace'))
            if not tokens(row['name'])<=tokens(' '.join(doc.text)): continue
            for href,label in doc.links:
                target=public_url(clean_url(record.get('final_url') or safe,href))
                if target and host(target)!=host(safe) and re.search('website|webseite|homepage|internet|www\\.',label,re.I): targets.append(target)
        details[row['source_id']]['directory_outbound_targets']=targets[:3]
        return choose([extended_verify(row,url,crawl.client()) for url in list(dict.fromkeys(targets))[:3]])
    union=record('directory_outbound_links',parallel([r for r in rows if r['source_id'] not in union],outbound),len(rows)-len(union))
    # A deterministic residual subset keeps the advanced-search spend bounded and comparable.
    remaining=[r for r in rows if r['source_id'] not in union]
    remaining.sort(key=lambda r:hashlib.sha256(('advanced-v1:'+r['source_id']).encode()).hexdigest())
    chosen=remaining[:25]
    def advanced(row):
        locality=row['tags'].get('addr:city') or 'Karlsruhe Region'
        q=f'{row["name"]} {locality} offizielle Website Kontakt'
        hits=search.search(q,'advanced');leads[row['source_id']].extend(h['url'] for h in hits)
        eligible=[h for h in hits if public_url(h['url'])][:3]
        return choose([extended_verify(row,h['url'],crawl.client()) for h in eligible])
    details['advanced_subset']=[r['source_id'] for r in chosen]
    union=record('advanced_search_25_residuals',parallel(chosen,advanced),len(chosen))
    save(ROOT/'all-leads.json',leads)
    save(ROOT/args.output,payload(complete=True))
    print('Finished: total',len(union),'of',len(rows),'credits',search.spent,'HTTP',crawl.budget.used,flush=True)


if __name__=='__main__': main()
