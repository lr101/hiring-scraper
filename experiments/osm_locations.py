"""Discover named OSM business locations within a city radius or German state."""
import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hiring_scraper.geography import (
    build_osm_radius_query,
    build_osm_state_query,
    haversine_m,
    normalize_german_state,
    osm_element_coordinates,
    resolve_nominatim_city,
)
from experiments.probe import probe


def _slug(value):
    value = value.casefold().replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    return re.sub(r'[^a-z0-9]+', '-', value).strip('-')


def _geocode_city(city, out):
    params = {
        'q': f'{city}, Deutschland', 'countrycodes': 'de', 'featureType': 'city',
        'format': 'jsonv2', 'addressdetails': 1, 'limit': 1,
    }
    url = 'https://nominatim.openstreetmap.org/search?' + urlencode(params)
    meta, body = probe(url, out, 'nominatim_city', {'Accept': 'application/json'})
    if meta.get('status') != 200:
        raise ValueError(f'Nominatim request failed: {meta.get("status", meta.get("error"))}')
    return resolve_nominatim_city(json.loads(body), city)


def _candidate(element, center=None, radius_m=None):
    point = osm_element_coordinates(element)
    if point is None:
        return None
    distance = None
    if center:
        distance = round(haversine_m(center[0], center[1], point[0], point[1]), 1)
        if distance > radius_m:
            return None
    tags = element.get('tags', {})
    if not tags.get('name'):
        return None
    return {
        'name': tags['name'],
        'website': tags.get('website') or tags.get('contact:website') or '',
        'email': tags.get('email') or tags.get('contact:email') or '',
        'category': tags.get('office') or tags.get('craft') or tags.get('industrial') or '',
        'lat': point[0],
        'lon': point[1],
        'distance_m': distance,
        'osm_type': element.get('type', ''),
        'osm_id': element.get('id'),
        'source_url': f"https://www.openstreetmap.org/{element.get('type')}/{element.get('id')}",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--city', help='German city to geocode and use as the radius center')
    scope.add_argument('--state', help='One of Germany’s 16 federal states')
    parser.add_argument('--lat', type=float, help='Use this city latitude instead of geocoding')
    parser.add_argument('--lon', type=float, help='Use this city longitude instead of geocoding')
    parser.add_argument('--radius-m', type=int, default=5000, help='City-centered radius in metres (default: 5000)')
    parser.add_argument('--office-value', help='Optional state query filter, e.g. it; keeps broad state requests bounded')
    parser.add_argument('--out', type=Path, required=True, help='New output directory; existing directories are refused')
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error('output directory already exists; choose a new path')
    if bool(args.lat is not None) != bool(args.lon is not None):
        parser.error('--lat and --lon must be supplied together')
    if args.state and (args.lat is not None or args.lon is not None):
        parser.error('--lat/--lon can only be used with --city')
    if args.city and args.office_value:
        parser.error('--office-value is only supported with --state')
    if args.city and (args.radius_m <= 0 or args.radius_m > 200_000):
        parser.error('--radius-m must be in the range 1 to 200000')

    args.out.mkdir(parents=True)
    if args.city:
        if args.lat is None:
            geocoded = _geocode_city(args.city, args.out)
            center = (geocoded['lat'], geocoded['lon'])
        else:
            geocoded = None
            center = (args.lat, args.lon)
        query = build_osm_radius_query(*center, args.radius_m)
        scope_record = {
            'kind': 'city_radius', 'city': args.city, 'center_lat': center[0],
            'center_lon': center[1], 'radius_m': args.radius_m, 'geocode': geocoded,
        }
        label = f'{_slug(args.city)}-{args.radius_m}m'
    else:
        try:
            state = normalize_german_state(args.state)
        except ValueError as error:
            parser.error(str(error))
        query = build_osm_state_query(state, args.office_value)
        scope_record = {'kind': 'state', 'state': state, 'office_value': args.office_value}
        center = None
        label = _slug(state)

    (args.out / 'overpass.ql').write_text(query + '\n')
    url = 'https://overpass-api.de/api/interpreter?' + urlencode({'data': query})
    meta, body = probe(url, args.out, 'osm', {'Accept': 'application/json'})
    candidates, parse_error, overpass_remark = [], None, None
    if meta.get('status') == 200 and not meta.get('truncated'):
        try:
            payload = json.loads(body)
            overpass_remark = payload.get('remark')
            if not overpass_remark:
                for element in payload.get('elements', []):
                    row = _candidate(element, center, args.radius_m if center else None)
                    if row:
                        candidates.append(row)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            parse_error = str(error)
    elif meta.get('truncated'):
        parse_error = 'Overpass response exceeded the 5 MB capture limit'
    candidates.sort(key=lambda row: (row['distance_m'] is None, row['distance_m'] or 0, row['name'].casefold()))
    (args.out / 'candidates.json').write_text(json.dumps(candidates, indent=2, ensure_ascii=False) + '\n')
    with (args.out / 'candidates.csv').open('w', newline='') as f:
        columns = ['name','website','email','category','lat','lon','distance_m','osm_type','osm_id','source_url']
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(candidates)
    summary = {
        'scope': scope_record,
        'source': 'OpenStreetMap via Overpass API',
        'request': {key: meta.get(key) for key in ('status','final_url','content_type','bytes','sha256','elapsed_seconds','truncated','error')},
        'query_sha256': hashlib.sha256(query.encode()).hexdigest(),
        'complete': meta.get('status') == 200 and not meta.get('truncated') and not overpass_remark and not parse_error,
        'overpass_remark': overpass_remark,
        'parse_error': parse_error,
        'candidates': len(candidates),
        'with_website': sum(bool(row['website']) for row in candidates),
        'distinct_website_hosts': len({host for row in candidates if (host := (urlsplit(row['website']).hostname or '').lower().removeprefix('www.'))}),
    }
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0 if summary['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
