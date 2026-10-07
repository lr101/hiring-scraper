"""Bounded Karlsruhe source comparison. Research prototype, not a scheduled crawler."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from probe import probe

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--city', default='Karlsruhe')
parser.add_argument('--lat', type=float, default=49.0069)
parser.add_argument('--lon', type=float, default=8.4037)
parser.add_argument('--radius-m', type=int, default=5000)
parser.add_argument('--out', default='data/karlsruhe')
args = parser.parse_args()
# Explicit API queries are supported by Overpass's API usage documentation;
# robots.txt disallows general indexing of /api/, not documented API clients.
query = f'''[out:json][timeout:30];
(nwr(around:{args.radius_m},{args.lat},{args.lon})[name][office];
nwr(around:{args.radius_m},{args.lat},{args.lon})[name][craft];
nwr(around:{args.radius_m},{args.lat},{args.lon})[name][industrial];);
out center tags;'''
Path(args.out).mkdir(parents=True, exist_ok=True)
Path(args.out, 'overpass.ql').write_text(query+'\n')
requests = [
 ('ba_web', 'https://www.arbeitsagentur.de/jobsuche/suche?'+urlencode({'wo':args.city,'umkreis':args.radius_m//1000,'angebotsart':1,'pav':'false'}), {}),
 ('ba_api_v6','https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs?'+urlencode({'wo':args.city,'umkreis':args.radius_m//1000,'angebotsart':1,'pav':'false','page':1,'size':25}), {'X-API-Key':'jobboerse-jobsuche'}),
 ('osm', 'https://overpass-api.de/api/interpreter?'+urlencode({'data':query}), {}),
 ('kit_robots','https://www.careerserviceportal.kit.edu/robots.txt', {}),
 ('kit_directory','https://www.careerserviceportal.kit.edu/de/jobboerse/unternehmensverzeichnis/', {}),
 ('cyberforum_robots','https://www.cyberforum.de/robots.txt', {}),
 ('cyberforum_directory','https://www.cyberforum.de/mitglieder', {}),
]
for label, url, headers in requests:
    meta, body = probe(url, args.out, label, headers)
    if label == 'osm' and meta.get('status') == 200:
        payload = json.loads(body)
        if payload.get('remark'):
            raise ValueError('Overpass incomplete: '+payload['remark'])
        sample = [e for e in payload['elements'] if e['tags'].get('office') == 'it'
                  and (e['tags'].get('website') or e['tags'].get('contact:website'))][:12]
        Path(args.out, 'osm_sample.json').write_text(json.dumps(sample, indent=2, ensure_ascii=False)+'\n')
    time.sleep(1)
