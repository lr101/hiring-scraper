"""Apply the explicit positive audit to expanded PoC results; no network or secrets."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path('data/website-discovery/expanded')


def key(item):
    return item['source_id'], urlsplit(item['website_url']).netloc.lower().removeprefix('www.')


def main():
    rows = json.loads((ROOT/'cohort.json').read_text())
    assert len(rows) == 120 and len({r['source_id'] for r in rows}) == 120
    by_id = {r['source_id']:r for r in rows}
    results = json.loads((ROOT/'completed-results.json').read_text())
    assert results['complete'] and not results['http_budget_reached']
    audit = json.loads((ROOT/'manual-audit.json').read_text())
    reviewed = {key(r):r for r in audit['entries']}
    union = {}; stages = []
    baseline_union = {}
    for name, entries in results['stages'].items():
        assert all(key(r) in reviewed for r in entries.values()), 'Every proposal needs an audit'
        accepted = {sid:r for sid,r in entries.items() if reviewed[key(r)]['status'] == 'confirmed'}
        added = set(accepted)-set(union); union.update(accepted)
        if name in {'mapped_wikidata','production_baseline'}:
            baseline_union.update(accepted)
        counts = results['counts'][name]
        stages.append({'method':name,'residual_rows_considered':counts['eligible_count'],
                       'raw_proposals':len(entries),'confirmed_added':len(added),
                       'false_matches':sum(reviewed[key(r)]['status']=='false_match' for r in entries.values()),
                       'review_needed':sum(reviewed[key(r)]['status']=='review_needed' for r in entries.values()),
                       'cumulative_confirmed':len(union),
                       'search_queries_available':counts['search_queries_available'],
                       'search_queries_unavailable':counts['search_queries_unavailable']})
    paired = json.loads((ROOT/'paired-depth-results.json').read_text())
    cache = json.loads((ROOT/'search-cache.json').read_text())
    controls = {}
    for depth in ('basic','advanced'):
        proposals = [v[depth] for v in paired['paired'].values() if v[depth]]
        assert all(key(r) in reviewed for r in proposals)
        controls[depth] = {'tested_companies':len(paired['paired']),
                           'confirmed_homepages':sum(reviewed[key(r)]['status']=='confirmed' for r in proposals),
                           'reported_acquisition_credits':sum(cache[depth+':'+v['query']]['credits'] for v in paired['paired'].values())}
    assert all((not p['basic'] and not p['advanced']) or
               (p['basic'] and p['advanced'] and key(p['basic'])==key(p['advanced']))
               for p in paired['paired'].values())
    structured = json.loads((ROOT/'structured-owner-results.json').read_text())
    assert all(key(r) in reviewed for r in structured['accepted'].values())
    schema = {'eligible_companies':structured['eligible_rows'],'raw_proposals':len(structured['accepted']),
              'confirmed_homepages':sum(reviewed[key(r)]['status']=='confirmed' for r in structured['accepted'].values()),
              'directory_false_matches':sum(reviewed[key(r)]['status']=='false_match' for r in structured['accepted'].values())}
    first = json.loads((ROOT/'initial-capped-results.json').read_text())
    completion = json.loads((ROOT/'crawl-completion-results.json').read_text())
    total_http = first['live_website_requests']+completion['live_website_requests']+paired['live_website_requests']
    summary = {'complete':True,'corpus_size':len(rows),'population_missing':645,
               'baseline_confirmed':len(baseline_union),'combined_confirmed':len(union),'additional_confirmed':len(union)-len(baseline_union),
               'baseline_missing':len(rows)-len(baseline_union),'combined_missing':len(rows)-len(union),
               'sample':{'size':sum(r['group']=='sample' for r in rows),'baseline_confirmed':sum(by_id[s]['group']=='sample' for s in baseline_union),'combined_confirmed':sum(by_id[s]['group']=='sample' for s in union)},
               'diagnostic':{'size':sum(r['group']=='diagnostic' for r in rows),'baseline_confirmed':sum(by_id[s]['group']=='diagnostic' for s in baseline_union),'combined_confirmed':sum(by_id[s]['group']=='diagnostic' for s in union)},
               'stages':stages,'paired_search_depth':controls,'structured_data_hypothesis':schema,
               'cost':{'search_api_calls_including_reused_prior':len(cache),
                       'reported_credits_new_study':sum(v['credits'] for v in cache.values() if not v['prior_run']),
                       'reported_credits_prior_reused':sum(v['credits'] for v in cache.values() if v['prior_run']),
                       'reported_credits_total_acquisition':sum(v['credits'] for v in cache.values()),
                       'nominal_credits_at_basic_1_advanced_2':sum(2 if v['depth']=='advanced' else 1 for v in cache.values()),
                       'live_website_attempts_including_robots_redirects':total_http,'osm_multiget_requests':5},
               'confirmed_homepages':union,
               'limitations':['Sequential residual pipeline, except paired-depth control; stage figures are incremental yields.',
                              'Three changed advanced-stage selections lack cached queries; use complete fixed 25-company paired control for depth comparison.',
                              'Single-reviewer audit of proposed positives; no full negative ground truth, no recall estimate.',
                              'Organization homepage counts include official parent/brand sites; exact branch address is not always current.',
                              'Censored first crawl retained for provenance; final matches reproduced with additional crawl allowance.',
                              'Baseline uses production matching policy with expanded research budgets, not production batch caps.']}
    (ROOT/'audited-summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:summary[k] for k in ('corpus_size','baseline_confirmed','combined_confirmed','additional_confirmed','cost')},indent=2))


if __name__ == '__main__': main()
