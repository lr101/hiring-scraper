"""Exercise the deployed PostgreSQL app using isolated CI data."""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from dotenv import dotenv_values
from sqlalchemy import func, select, text

database_password = dotenv_values('/run/app-bootstrap.env').get('HIRING_DB_PASSWORD')
if not database_password:
    raise RuntimeError('HIRING_DB_PASSWORD is missing from the CI bootstrap environment file')
os.environ['DATABASE_URL'] = (
    f"postgresql+psycopg://hiring_app:{quote(database_password, safe='')}@db:5432/hiring"
)

from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.models import Company, Job, JobFeed
from hiring_scraper.app.seed import import_fixture

WEB = 'http://app:8000'
API = 'http://app:8000'


def read(url: str, *, method: str = 'GET', payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=data, method=method,
                      headers={} if data is None else {'Content-Type': 'application/json'})
    with urlopen(request, timeout=60) as response:
        body = response.read()
        if 'application/json' in response.headers.get('Content-Type', ''):
            return json.loads(body)
        return body.decode()


def database_counts() -> dict:
    with SessionLocal() as session:
        assert session.bind.dialect.name == 'postgresql'
        assert session.scalar(text('SELECT current_user')) == 'hiring_app'
        assert session.scalar(text('SELECT postgis_version()'))
        return {name: session.scalar(select(func.count()).select_from(model))
                for name, model in [('companies', Company), ('feeds', JobFeed), ('jobs', Job)]}


def main() -> None:
    assert read(API + '/health/ready') == {'status': 'ready'}
    page = read(WEB + '/')
    assert 'id="root"' in page
    assets = re.findall(r'(?:src|href)="([^\"]+\.(?:js|css))"', page)
    assert any(asset.endswith('.js') for asset in assets)
    assert any(asset.endswith('.css') for asset in assets)
    for asset in assets:
        assert read(WEB + asset)
    assert 'id="root"' in read(WEB + '/profile')

    before = database_counts()
    assert before == {'companies': 0, 'feeds': 0, 'jobs': 0}, 'Fresh startup imported fixture data'
    assert read(WEB + '/api/v1/companies?limit=1')['total'] == 0

    imported = import_fixture(Path('fixtures/karlsruhe-osm-candidates.csv'),
                              Path('fixtures/karlsruhe-career-enrichment.json'))
    after_import = database_counts()
    assert imported == after_import and imported['companies'] > 0 and imported['jobs'] > 0
    assert read(WEB + '/api/v1/companies?limit=1')['total'] > 0

    repeated = import_fixture(Path('fixtures/karlsruhe-osm-candidates.csv'),
                              Path('fixtures/karlsruhe-career-enrichment.json'))
    assert database_counts() == after_import == repeated, 'Repeat import changed record counts'

    profile = json.loads(Path('fixtures/cv_profile.json').read_text())
    profile_id = None
    try:
        saved = read(WEB + '/api/v1/profiles', method='POST', payload=profile)
        profile_id = saved['id']
        assert saved['language_levels']['German'] == 'B2'
        assert saved['search_area']['radius_km'] == 35
        query = urlencode({'profile_id': profile_id, 'min_match_score': 0, 'limit': 3})
        board = read(WEB + '/api/v1/jobs?' + query)
        assert board['total'] > 3
        assert board['location']['city'] == 'Heidelberg'
        assert board['location']['country_code'] == 'DE'
        assert board['location']['radius_km'] == 35
        counts = board['counts']
        assert counts['recommended'] + counts['possible'] + counts['unlikely'] == board['total']
        next_page = read(WEB + '/api/v1/jobs?' + query + '&offset=3')
        assert not ({job['id'] for job in board['items']} &
                    {job['id'] for job in next_page['items']})
        for job in board['items']:
            detail = read(WEB + f"/api/v1/jobs/{job['id']}?profile_id={profile_id}&country_code=DE")
            assert job['profile_match'] == detail['profile_match']
            assert 'id="root"' in read(WEB + f"/jobs/{job['id']}")
        changed = copy.deepcopy(profile)
        changed['language_levels']['French'] = 'B1'
        changed['search_area']['radius_km'] = 36
        edited = read(WEB + f'/api/v1/profiles/{profile_id}', method='PUT', payload=changed)
        assert edited['language_levels']['French'] == 'B1'
        assert edited['search_area']['radius_km'] == 36
        persisted = next(row for row in read(WEB + '/api/v1/profiles')['items']
                         if row['id'] == profile_id)
        assert persisted['language_levels']['French'] == 'B1'
        assert persisted['search_area']['radius_km'] == 36
        invalid = copy.deepcopy(changed)
        invalid['language_levels']['German'] = 'D3'
        try:
            read(WEB + f'/api/v1/profiles/{profile_id}', method='PUT', payload=invalid)
        except HTTPError as error:
            assert error.code == 422
        else:
            raise AssertionError('Invalid language level was accepted')
        country = read(WEB + f'/api/v1/jobs?profile_id={profile_id}&country_code=US&limit=1')
        assert country['location']['country_code'] == 'US'
    finally:
        if profile_id is not None:
            assert read(WEB + f'/api/v1/profiles/{profile_id}', method='DELETE')['deleted']
            assert all(row['id'] != profile_id for row in read(WEB + '/api/v1/profiles')['items'])
    print(json.dumps({'result': 'pass', 'database': 'PostgreSQL/PostGIS',
                      'application_role': 'hiring_app', 'fresh_database': before,
                      'repeat_import': repeated,
                      'checks': ['empty fresh startup', 'static assets and SPA', 'same-origin API',
                                 'profile create/edit/validation/delete', 'regional defaults',
                                 'unique pagination', 'list/detail agreement', 'country override']}, indent=2))


if __name__ == '__main__':
    main()
