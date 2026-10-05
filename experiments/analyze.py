"""Rebuild candidate tables from captured responses; no network requests."""
import csv
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path('data/karlsruhe')

def write_csv(name, rows, fields):
    with (BASE / name).open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)

def main():
    osm = json.loads((BASE/'osm.body').read_text())
    if osm.get('remark'):
        raise ValueError('Overpass reported an incomplete result: '+osm['remark'])
    rows=[]
    for e in osm['elements']:
        t=e['tags']; coord=e.get('center',e)
        rows.append(dict(name=t['name'],website=t.get('website',t.get('contact:website','')),
                         source_url=f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
                         category=t.get('office',t.get('craft',t.get('industrial',''))),
                         lat=coord.get('lat'),lon=coord.get('lon')))
    write_csv('osm_candidates.csv',rows,['name','website','source_url','category','lat','lon'])
    ba=json.loads((BASE/'ba_api_v6.body').read_text())
    jobs=ba['ergebnisliste']
    ba_rows=[dict(name=j['firma'],reference=j['referenznummer'],external_url=j.get('externeURL',''),
                  locations='; '.join(x.get('adresse',{}).get('ort','') for x in j.get('stellenlokationen',[]))) for j in jobs]
    write_csv('ba_candidates.csv',ba_rows,['name','reference','external_url','locations'])
    ssr=json.loads(re.search(r'<script id="ng-state" type="application/json">(.*?)</script>',(BASE/'ba_web.body').read_text(),re.S)[1])['suchergebnis']['ergebnisliste']
    kit=[]
    # Deliberately tied to the observed directory markup; inspect on layout changes.
    for block in re.split(r'<div class="job-con [^"]*">',(BASE/'kit_directory.body').read_text())[1:]:
        name=re.search(r'<div class="job-box-hl">(.*?)</div>',block,re.S)
        location=re.search(r'<div class="icon-txt ic-pin_location">(.*?)</div>',block,re.S)
        homepage=re.search(r'<a href="([^"]+)"[^>]*>\s*<div class="icon-txt ic-home">Homepage',block,re.S)
        clean=lambda s: html.unescape(re.sub('<[^>]+>','',s)).strip()
        if name and location:
            kit.append(dict(name=clean(name[1]),location=clean(location[1]),website=html.unescape(homepage[1]) if homepage else ''))
    write_csv('kit_candidates.csv',kit,['name','location','website'])
    websites=[r['website'] for r in rows if r['website']]
    summary={'osm':{'elements':len(rows),'with_website':len(websites),
                    'distinct_exact_hosts':len({urlsplit(u).netloc for u in websites})},
             'ba_api':{'jobs_in_page':len(jobs),'reported_total':ba['maxErgebnisse'],
                       'unique_employer_names':len({j['firma'] for j in jobs}),
                       'external_urls':sum(bool(j.get('externeURL')) for j in jobs)},
             'ba_html':{'jobs_in_page':len(ssr),'unique_employer_names':len({j['firma'] for j in ssr})},
             'kit':{'parsed_cards':len(kit),'with_homepage':sum(bool(r['website']) for r in kit),
                    'karlsruhe_cards':sum(bool(re.search(r'\bKarlsruhe\b',r['location'])) for r in kit),
                    'karlsruhe_with_homepage':sum(bool(re.search(r'\bKarlsruhe\b',r['location'])) and bool(r['website']) for r in kit)}}
    (BASE/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    main()
