"""Exercise the deployed PostgreSQL app using isolated CI data."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
import re
import time
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from dotenv import dotenv_values
from sqlalchemy import create_engine, func, select, text

from migration_heads import assert_migration_heads

bootstrap_environment = dotenv_values('/run/app-bootstrap.env')
database_password = bootstrap_environment.get('HIRING_DB_PASSWORD')
if not database_password:
    raise RuntimeError('HIRING_DB_PASSWORD is missing from the CI bootstrap environment file')
postgres_password = bootstrap_environment.get('POSTGRES_PASSWORD')
if not postgres_password:
    raise RuntimeError('POSTGRES_PASSWORD is missing from the CI bootstrap environment file')
os.environ['DATABASE_URL'] = (
    f"postgresql+psycopg://hiring_app:{quote(database_password, safe='')}@db:5432/hiring"
)

from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.models import Company, Job, JobFeed, utcnow
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
        provider, recorded, actual = session.execute(text(
            'SELECT datlocprovider, datcollversion, pg_database_collation_actual_version(oid) '
            'FROM pg_database WHERE datname = current_database()'
        )).one()
        assert provider == 'c', f'Expected the existing libc collation provider, got {provider!r}'
        assert actual is not None and recorded == actual, (
            f'Database collation version is stale or unavailable: {recorded!r} != {actual!r}'
        )
        return {name: session.scalar(select(func.count()).select_from(model))
                for name, model in [('companies', Company), ('feeds', JobFeed), ('jobs', Job)]}


def exercise_company_domain_index() -> None:
    # A lease-only update still inserts a new tuple into every index when the
    # indexed lease column changes, including ix_companies_domain.
    with SessionLocal.begin() as session:
        company = session.scalars(select(Company).limit(1)).first()
        assert company is not None, 'The company fixture was not imported'
        company.discovery_lease_until = utcnow() + timedelta(minutes=1)


def exercise_collation_repair_migration() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    # Force stale catalog metadata, then run the real migration on the fresh
    # CI database. Rewinding newer empty-table migrations lets CI exercise the
    # historical repair against every current migration without deleting data.
    admin_url = (
        f"postgresql+psycopg://postgres:{quote(postgres_password, safe='')}@db:5432/hiring"
    )
    admin_engine = create_engine(admin_url)
    reindex_probe_before = None
    try:
        with admin_engine.connect() as connection:
            assert_migration_heads(connection, config)
            counts = connection.execute(text(
                'SELECT (SELECT count(*) FROM user_profiles), '
                '(SELECT count(*) FROM profile_cvs), '
                '(SELECT count(*) FROM job_applications)'
            )).one()
            assert counts == (0, 0, 0), (
                'The collation migration smoke must run before fixture or user data is added'
            )

        app_database_url = os.environ['DATABASE_URL']
        os.environ['DATABASE_URL'] = admin_url
        try:
            command.downgrade(config, '0026_weekly_goal_environment')
        finally:
            os.environ['DATABASE_URL'] = app_database_url

        with admin_engine.begin() as connection:
            # Alembic represents the mergepoint downgrade as both parent
            # revisions. Keep the app/journey parent applied and mark only the
            # collation branch as pending so this upgrade executes 0025 again.
            deleted = connection.execute(text(
                "DELETE FROM alembic_version "
                "WHERE version_num = '0025_postgres_collation_repair'"
            ))
            assert deleted.rowcount == 1, (
                'Expected the PostgreSQL collation revision at the migration branchpoint'
            )
            connection.execute(text(
                'CREATE TABLE public.ci_collation_reindex_probe (value text NOT NULL)'
            ))
            connection.execute(text(
                "INSERT INTO public.ci_collation_reindex_probe (value) "
                "VALUES ('Karlsruhe'), ('München'), ('Århus')"
            ))
            connection.execute(text(
                'CREATE INDEX ci_collation_reindex_probe_idx '
                'ON public.ci_collation_reindex_probe (value)'
            ))
            reindex_probe_before = connection.scalar(text(
                "SELECT pg_relation_filenode('public.ci_collation_reindex_probe_idx'::regclass)"
            ))
            assert reindex_probe_before is not None, 'Could not inspect the collation probe index'
            connection.execute(text(
                "UPDATE pg_database SET datcollversion = 'ci-forced-stale' "
                'WHERE datname = current_database()'
            ))
            recorded = connection.scalar(text(
                'SELECT datcollversion FROM pg_database WHERE datname = current_database()'
            ))
            assert recorded == 'ci-forced-stale', 'Could not force stale collation metadata'

        with admin_engine.connect() as connection:
            revisions = set(connection.scalars(text('SELECT version_num FROM alembic_version')))
            assert revisions == {'0026_weekly_goal_environment'}, (
                f'Expected only the application branch applied, got {revisions!r}'
            )

        app_database_url = os.environ['DATABASE_URL']
        os.environ['DATABASE_URL'] = admin_url
        try:
            # Hold the probe table so the concurrent reindex pauses. While it
            # waits, the recorded collation version must still be stale; this
            # checks the ordering through PostgreSQL's observable behavior.
            with ThreadPoolExecutor(max_workers=1) as executor:
                with admin_engine.connect() as blocker:
                    blocking_transaction = blocker.begin()
                    try:
                        blocker.execute(text(
                            'LOCK TABLE public.ci_collation_reindex_probe '
                            'IN ACCESS EXCLUSIVE MODE'
                        ))
                        upgrade = executor.submit(command.upgrade, config, 'head')
                        deadline = time.monotonic() + 60
                        while time.monotonic() < deadline:
                            if upgrade.done():
                                upgrade.result()
                                raise AssertionError(
                                    'Collation repair did not wait to reindex the probe table'
                                )
                            with admin_engine.connect() as observer:
                                waiting_on_reindex = observer.scalar(text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE datname = current_database() "
                                    "AND pid <> pg_backend_pid() "
                                    "AND query ILIKE 'REINDEX DATABASE CONCURRENTLY%' "
                                    "AND wait_event_type = 'Lock')"
                                ))
                                version_during_reindex = observer.scalar(text(
                                    'SELECT datcollversion FROM pg_database '
                                    'WHERE datname = current_database()'
                                ))
                            if waiting_on_reindex:
                                assert version_during_reindex == 'ci-forced-stale', (
                                    'Collation version was refreshed before index rebuilding '
                                    'completed'
                                )
                                break
                            time.sleep(0.05)
                        else:
                            raise AssertionError(
                                'Collation repair did not reach the blocked probe index'
                            )
                    finally:
                        blocking_transaction.rollback()
                upgrade.result(timeout=300)
        finally:
            os.environ['DATABASE_URL'] = app_database_url

        with admin_engine.connect() as connection:
            recorded, actual = connection.execute(text(
                'SELECT datcollversion, pg_database_collation_actual_version(oid) '
                'FROM pg_database WHERE datname = current_database()'
            )).one()
            assert actual is not None and recorded == actual, (
                f'Collation migration did not refresh the version: {recorded!r} != {actual!r}'
            )
            reindex_probe_after = connection.scalar(text(
                "SELECT pg_relation_filenode('public.ci_collation_reindex_probe_idx'::regclass)"
            ))
            assert reindex_probe_after is not None and reindex_probe_after != reindex_probe_before, (
                'Collation migration did not rebuild an existing text index'
            )
            assert_migration_heads(connection, config)
        with admin_engine.begin() as connection:
            connection.execute(text(
                'DROP TABLE public.ci_collation_reindex_probe'
            ))
    finally:
        admin_engine.dispose()


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

    exercise_collation_repair_migration()
    imported = import_fixture(Path('fixtures/karlsruhe-osm-candidates.csv'),
                              Path('fixtures/karlsruhe-career-enrichment.json'))
    exercise_company_domain_index()
    after_import = database_counts()
    assert imported == after_import and imported['companies'] > 0 and imported['jobs'] > 0
    assert read(WEB + '/api/v1/companies?limit=1')['total'] > 0

    repeated = import_fixture(Path('fixtures/karlsruhe-osm-candidates.csv'),
                              Path('fixtures/karlsruhe-career-enrichment.json'))
    assert database_counts() == after_import == repeated, 'Repeat import changed record counts'

    profile = json.loads(Path('fixtures/cv_profile.json').read_text())
    profile['search_area'].update({
        'label': 'Karlsruhe, Germany', 'city': 'Karlsruhe',
        'latitude': 49.0068705, 'longitude': 8.4034195,
    })
    profile_id = None
    try:
        saved = read(WEB + '/api/v1/profiles', method='POST', payload=profile)
        profile_id = saved['id']
        assert saved['language_levels']['German'] == 'B2'
        assert saved['search_area']['radius_km'] == 35
        query = urlencode({'profile_id': profile_id, 'limit': 3})
        board = read(WEB + '/api/v1/jobs?' + query)
        if board.get('search_mode') == 'warming':
            from hiring_scraper.app.api import process_search_snapshot_refresh_once
            for _ in range(20):
                if process_search_snapshot_refresh_once():
                    break
                time.sleep(0.25)
            else:
                raise AssertionError('Queued job search snapshot was not claimed')
            board = read(WEB + '/api/v1/jobs?' + query)
        assert board['total'] > 0
        assert board['location']['city'] == 'Karlsruhe'
        assert board['location']['country_code'] == 'DE'
        assert board['location']['radius_km'] == 35
        assert board['profile_id'] == profile_id
        assert all('profile_match' not in job for job in board['items'])
        next_page = read(WEB + '/api/v1/jobs?' + query + '&offset=3')
        assert not ({job['id'] for job in board['items']} &
                    {job['id'] for job in next_page['items']})
        for job in board['items']:
            detail = read(WEB + f"/api/v1/jobs/{job['id']}?profile_id={profile_id}&country_code=DE")
            assert 'profile_match' in detail
            assert job['geography'] == detail['geography']
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
                                 'stale collation repair migration and domain index update',
                                 'profile create/edit/validation/delete', 'regional defaults',
                                 'unique pagination', 'list/detail agreement', 'country override']}, indent=2))


if __name__ == '__main__':
    main()
