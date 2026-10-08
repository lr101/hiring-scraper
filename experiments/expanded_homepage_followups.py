"""Paired search-depth and structured-profile hypotheses for the expanded PoC.

Exact JSON-LD profile identity is experimental evidence, not owner verification.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from experiments.expanded_homepage_poc import (
    ROOT, SearchLab, CrawlLab, save, parallel, extended_verify, choose, normalized,
)
from hiring_scraper.pages import Document
from hiring_scraper.website_discovery import host, public_url, root


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def structured_owner(row, url, doc):
    tags = row['tags']
    if not all(tags.get(k) for k in ('addr:city', 'addr:postcode', 'addr:street', 'addr:housenumber')):
        return None
    for kind, script in doc.scripts:
        if kind != 'application/ld+json':
            continue
        try:
            data = json.loads(script)
        except (ValueError, TypeError):
            continue
        for item in objects(data):
            types = item.get('@type', [])
            if isinstance(types, str): types = [types]
            if not isinstance(types, list) or not any(t in {'Organization', 'LocalBusiness', 'Corporation', 'ProfessionalService', 'RealEstateAgent', 'InsuranceAgency'} for t in types):
                continue
            own_url = item.get('url')
            if not isinstance(own_url, str) or not public_url(own_url) or host(own_url) != host(url):
                continue
            names = [item.get('name'), item.get('legalName')]
            if not any(isinstance(n, str) and normalized(n) == normalized(row['name']) for n in names):
                continue
            address = item.get('address')
            if not isinstance(address, dict): continue
            city = normalized(str(address.get('addressLocality') or ''))
            postcode = str(address.get('postalCode') or '')
            street = normalized(str(address.get('streetAddress') or ''))
            if city != normalized(tags['addr:city']) or postcode != str(tags['addr:postcode']): continue
            expected = normalized(tags['addr:street'] + ' ' + tags['addr:housenumber'])
            if street != expected: continue
            return {'source_id': row['source_id'], 'website_url': root(url), 'evidence_url': url,
                    'method': 'jsonld_exact_owner_and_full_address'}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    rows = json.loads((ROOT/'cohort.json').read_text())
    by_id = {r['source_id']: r for r in rows}
    initial = json.loads((ROOT/'initial-capped-results.json').read_text())
    ids = initial['details']['advanced_subset']
    search = SearchLab(0 if args.offline else 25)
    crawl = CrawlLab(args.offline, http_limit=750)
    paired = {}
    def trial(row):
        q = f'{row["name"]} {row["tags"].get("addr:city") or "Karlsruhe Region"} offizielle Website Kontakt'
        outcomes = {}
        for depth in ('basic', 'advanced'):
            hits = search.search(q, depth)
            eligible = [h for h in hits if public_url(h['url'])][:3]
            outcomes[depth] = choose([extended_verify(row, h['url'], crawl.client()) for h in eligible])
        return row['source_id'], {'query': q, **outcomes}
    paired = dict(parallel([by_id[sid] for sid in ids], trial))
    paired_output = 'paired-depth-replay.json' if args.offline else 'paired-depth-results.json'
    save(ROOT/paired_output, {'offline':args.offline, 'paired':paired,
         'basic_accepted':sum(bool(v['basic']) for v in paired.values()),
         'advanced_accepted':sum(bool(v['advanced']) for v in paired.values()),
         'new_search_credits':search.spent, 'live_website_requests':crawl.budget.used})
    print('Paired search-depth control finished', flush=True)
    leads = json.loads((ROOT/'all-leads.json').read_text())
    client = CrawlLab(True).client()
    matched = {}; eligible_rows = 0; inspected_pages = 0; jsonld_pages = 0
    for row in rows:
        if not all(row['tags'].get(k) for k in ('addr:city','addr:postcode','addr:street','addr:housenumber')):
            continue
        eligible_rows += 1
        approved = []
        domains = {host(u) for u in leads[row['source_id']] if public_url(u)}
        for url, (record, body) in client.cache.items():
            if host(url) not in domains or record.get('state') != 'ok' or 'html' not in record.get('content_type','') or url.endswith('/robots.txt'):
                continue
            # Apply robots policy even on shared/reused captures.
            allowed, _ = client.get(url)
            if allowed.get('state') != 'ok': continue
            inspected_pages += 1
            doc = Document(); doc.feed(body.decode('utf8', errors='replace'))
            jsonld_pages += int(any(kind == 'application/ld+json' for kind,_ in doc.scripts))
            result = structured_owner(row,url,doc)
            if result: approved.append(result)
        selected = choose(approved)
        if selected: matched[row['source_id']] = selected
    save(ROOT/'structured-owner-results.json', {'eligible_rows':eligible_rows,
         'inspected_page_row_pairs':inspected_pages, 'jsonld_page_row_pairs':jsonld_pages,
         'accepted':matched, 'new_search_credits':0, 'live_website_requests':0})
    print('Structured owner control finished:', len(matched), flush=True)


if __name__ == '__main__': main()
