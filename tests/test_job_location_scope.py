"""Equivalence, fallback, backfill, and single-pass snapshot tests for precomputed job location scopes."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import and_, event, func, insert, or_, select, text, update
from sqlalchemy.orm import aliased, sessionmaker

from hiring_scraper.app import job_location_scope, models, search_snapshots, source_changes
from hiring_scraper.app.board_scope import german_state_bounds
from hiring_scraper.app.discovery_jobs import german_company_filter
from hiring_scraper.app.job_location_scope import (
    GERMANY_COUNTRY_CODE_PATTERNS,
    LOCATION_SCOPE_VERSION,
    backfill_job_location_scopes_once,
    germany_bounds,
    germany_text_patterns,
    state_text_patterns,
)
from hiring_scraper.app.job_search import replace_job_locations
from hiring_scraper.app.models import (
    Company,
    Job,
    JobFeed,
    JobLocation,
    SemanticSearchRefresh,
    UserProfile,
)
from hiring_scraper.geography import GERMAN_STATES
from tests import pg


def _legacy_state_text_candidates(state: str):
    """Exact pre-refactor SQL predicate (OR'd ILIKEs + per-pattern EXISTS + bounds)."""
    patterns = state_text_patterns(state)
    candidates = [
        *(Job.location_text.ilike(pattern) for pattern in patterns),
        *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in patterns),
    ]
    if bounds := german_state_bounds().get(state):
        west, south, east, north = bounds
        candidates.append(Job.locations.any(and_(
            JobLocation.latitude.between(south, north),
            JobLocation.longitude.between(west, east),
        )))
    return or_(*candidates)


def _legacy_germany_text_candidates():
    """Exact pre-refactor Germany SQL predicate (OR'd ILIKEs + per-pattern EXISTS + bounds)."""
    patterns = germany_text_patterns()
    candidates = [
        *(Job.location_text.ilike(pattern) for pattern in patterns),
        *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in patterns),
        *(Job.location_text.ilike(pattern) for pattern in GERMANY_COUNTRY_CODE_PATTERNS),
        *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in GERMANY_COUNTRY_CODE_PATTERNS),
    ]
    if bounds := germany_bounds():
        west, south, east, north = bounds
        candidates.append(Job.locations.any(and_(
            JobLocation.latitude.between(south, north),
            JobLocation.longitude.between(west, east),
        )))
    return or_(*candidates)


def _legacy_scoped_job_rows_statement(
    *,
    employer_country: str | None,
    state: str | None,
    location_scope: str,
    company_id: int | None,
    normalized_query: str | None,
    work_style: str,
    posting_status: str,
):
    """Exact pre-refactor candidate/source query for Germany and state scopes."""
    employer_company = aliased(Company)
    filters = [search_snapshots.posting_status_filter(posting_status)]
    if company_id is not None:
        filters.append(or_(JobFeed.company_id == company_id, Job.employer_company_id == company_id))
    if normalized_query:
        pattern = f"%{normalized_query}%"
        filters.append(or_(
            Job.title.ilike(pattern),
            Company.name.ilike(pattern),
            Company.domain.ilike(pattern),
            employer_company.name.ilike(pattern),
            employer_company.domain.ilike(pattern),
            Job.raw_metadata["hiring_organization"]["name"].as_string().ilike(pattern),
        ))
    if work_style == 'remote':
        filters.append(search_snapshots._remote_job_candidate())
    elif work_style in {'hybrid', 'onsite'}:
        filters.append(Job.work_arrangement == work_style)
    if employer_country:
        clauses = [
            german_company_filter(Company),
            Company.source == "default_job_feed",
            german_company_filter(employer_company),
            Job.locations.any(func.lower(JobLocation.country_code) == "de"),
            _legacy_germany_text_candidates(),
        ]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            clauses.append(search_snapshots._remote_job_candidate())
        filters.append(or_(*clauses))
    elif state:
        clauses = [
            Company.state == state,
            employer_company.state == state,
            _legacy_state_text_candidates(state),
        ]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            clauses.append(search_snapshots._remote_job_candidate())
        filters.append(or_(*clauses))
    return (
        select(Job.id, Job.feed_id)
        .join(Job.feed)
        .join(JobFeed.company)
        .outerjoin(employer_company, Job.employer_company_id == employer_company.id)
        .where(*filters)
        .order_by(Job.id.asc())
    )


class JobLocationScopeTests(unittest.TestCase):
    def setUp(self):
        self.engine = pg.engine(self)
        models.Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def _seed_equivalence_fixture(self) -> dict[str, int]:
        """Seed a varied corpus covering German/state text, umlauts, DE forms, coords, remote, and traps."""
        with self.factory.begin() as session:
            foreign_co = Company(
                source='test', source_id='foreign', name='Global Corp',
                domain='global.example', country_code='FR', state='Ile-de-France',
            )
            bavaria_co = Company(
                source='test', source_id='bavaria', name='Alpen GmbH',
                domain='alpen.example', country_code='DE', state='Bayern',
            )
            default_feed_co = Company(
                source='default_job_feed', source_id='default-feed', name='Feed Aggregator',
                domain='feed.example',
            )
            berlin_employer = Company(
                source='test', source_id='berlin-emp', name='Spree AG',
                domain='spree.example', country_code='DE', state='Berlin',
            )
            session.add_all([foreign_co, bavaria_co, default_feed_co, berlin_employer])
            session.flush()

            foreign_feed = JobFeed(
                company_id=foreign_co.id, provider='rss',
                feed_url='https://global.example/feed', status='parsed',
            )
            bavaria_feed = JobFeed(
                company_id=bavaria_co.id, provider='rss',
                feed_url='https://alpen.example/feed', status='parsed',
            )
            default_feed = JobFeed(
                company_id=default_feed_co.id, provider='rss',
                feed_url='https://feed.example/feed', status='parsed',
            )
            session.add_all([foreign_feed, bavaria_feed, default_feed])
            session.flush()

            # (key, feed, employer_company, location_text, locations, kwargs)
            # location tuple: (label, country_code, lat, lon)
            bw_west, bw_south, bw_east, bw_north = german_state_bounds()['Baden-Württemberg']
            de_west, de_south, de_east, de_north = germany_bounds()
            cases = [
                # German cities, umlauts, uppercase, transliterations, eszett edge cases
                ('city_munich_umlaut', foreign_feed, None, 'München', [], {}),
                ('city_munich_upper', foreign_feed, None, 'MÜNCHEN, Office', [], {}),
                ('city_muenchen_ascii', foreign_feed, None, 'Muenchen / Hybrid', [], {'work_arrangement': 'hybrid'}),
                ('city_koeln_upper', foreign_feed, None, 'KÖLN', [], {}),
                ('city_nuernberg_ascii', foreign_feed, None, 'Nuernberg Hub', [], {}),
                ('city_duesseldorf', foreign_feed, None, 'Düsseldorf', [], {}),
                ('city_freiburg', foreign_feed, None, 'Freiburg im Breisgau', [], {}),
                ('city_giessen_eszett', foreign_feed, None, 'Gießen', [], {}),
                ('city_giessen_ss', foreign_feed, None, 'Giessen', [], {}),
                ('city_dessau_eszett', foreign_feed, None, 'Dessau-Roßlau', [], {}),
                ('city_dessau_ss', foreign_feed, None, 'Dessau-Rosslau', [], {}),
                ('city_saarbruecken', foreign_feed, None, 'Saarbrücken', [], {}),
                ('city_dresden_label', foreign_feed, None, 'Central Europe', [('Dresden Campus', None, None, None)], {}),
                ('city_multiline_munich', foreign_feed, None, 'HQ\nMünchen', [], {}),
                # State names and English / ASCII aliases
                ('state_bavaria_en', foreign_feed, None, 'Bavaria', [], {}),
                ('state_nrw_hyphen', foreign_feed, None, 'Nordrhein-Westfalen', [], {}),
                ('state_bw_ascii', foreign_feed, None, 'Baden-Wuerttemberg', [], {}),
                ('state_lower_saxony', foreign_feed, None, 'Lower Saxony', [], {}),
                ('state_thuringia_label', foreign_feed, None, 'EMEA', [('Thuringia', None, None, None)], {}),
                ('state_rhineland', foreign_feed, None, 'Rhineland-Palatinate', [], {}),
                # DE country-code forms
                ('de_exact', foreign_feed, None, 'DE', [], {}),
                ('de_lower', foreign_feed, None, 'de', [], {}),
                ('de_comma_suffix', foreign_feed, None, 'SomeTown, DE', [], {}),
                ('de_parens', foreign_feed, None, 'SomeTown (DE)', [], {}),
                ('de_prefix_dash', foreign_feed, None, 'DE-80331 SomeTown', [], {}),
                ('de_prefix_space_dash', foreign_feed, None, 'DE - EMEA', [], {}),
                ('de_d_dash', foreign_feed, None, 'D-10115 Hub', [], {}),
                ('de_d_space_dash', foreign_feed, None, 'D- 80331 Hub', [], {}),
                ('de_slash_suffix', foreign_feed, None, 'Remote / DE', [], {}),
                ('de_slash_prefix', foreign_feed, None, 'DE/AT', [], {}),
                ('de_comma_period', foreign_feed, None, 'Office, DE.', [], {}),
                ('de_label_only', foreign_feed, None, 'EMEA', [('Campus (DE)', None, None, None)], {}),
                # Traps that MUST NOT match DE text patterns
                ('trap_denver', foreign_feed, None, 'Denver, CO', [], {}),
                ('trap_delaware', foreign_feed, None, 'Delaware, US', [], {}),
                ('trap_code', foreign_feed, None, 'Code Valley', [], {}),
                ('trap_adecco', foreign_feed, None, 'Adecco HQ', [], {}),
                ('trap_de_trailing_newline', foreign_feed, None, 'DE\n', [], {}),
                ('trap_bordeaux', foreign_feed, None, 'Bordeaux, France', [], {}),
                ('trap_paris', foreign_feed, None, 'Paris, France', [('Paris', 'FR', 48.8566, 2.3522)], {}),
                ('trap_milan', foreign_feed, None, 'Milan, Italy', [('Milan', 'IT', 45.4642, 9.1900)], {}),
                ('trap_zurich_in_de_bbox', foreign_feed, None, 'Zurich, Switzerland', [('Zurich', 'CH', 47.3769, 8.5417)], {}),
                ('trap_vienna', foreign_feed, None, 'Vienna, Austria', [('Vienna', 'AT', 48.2082, 16.3738)], {}),
                ('trap_empty', foreign_feed, None, '', [], {}),
                ('trap_none', foreign_feed, None, None, [], {}),
                # JobLocation.country_code
                ('cc_de_upper', foreign_feed, None, 'Unlisted Village', [('Unlisted', 'DE', None, None)], {}),
                ('cc_de_lower', foreign_feed, None, 'Unlisted Village', [('Unlisted', 'de', None, None)], {}),
                ('cc_fr_only', foreign_feed, None, 'Unlisted Village', [('Unlisted', 'FR', None, None)], {}),
                # Coordinates: inside state bbox, exact boundary, Germany bbox, outside, partial
                ('coords_bayern', foreign_feed, None, 'Plant South', [('Plant', None, 48.1351, 11.5820)], {}),
                ('coords_berlin', foreign_feed, None, 'Campus East', [('Campus', None, 52.5200, 13.4050)], {}),
                ('coords_bw_edge', foreign_feed, None, 'Border Site', [('Edge', None, bw_south, bw_west)], {}),
                ('coords_de_edge', foreign_feed, None, 'DE Edge', [('Edge', None, de_north, de_east)], {}),
                ('coords_outside_london', foreign_feed, None, 'UK Office', [('UK', 'GB', 51.5072, -0.1276)], {}),
                ('coords_partial_lat_only', foreign_feed, None, 'Unknown', [('Partial', None, 52.5200, None)], {}),
                # Multi-location jobs
                ('multi_foreign_and_hamburg', foreign_feed, None, 'Global', [
                    ('Paris', 'FR', 48.8566, 2.3522),
                    ('Hamburg', 'DE', 53.5511, 9.9937),
                ], {}),
                ('multi_two_states', foreign_feed, None, 'Multi', [
                    ('Kiel Office', None, None, None),
                    ('Erfurt Office', None, None, None),
                ], {}),
                # Remote / work_arrangement variants
                ('remote_arrangement', foreign_feed, None, 'Anywhere', [], {'work_arrangement': 'remote'}),
                ('remote_legacy_flag', foreign_feed, None, 'Anywhere', [], {'is_remote': True, 'work_arrangement': None}),
                ('onsite_with_is_remote_true', foreign_feed, None, 'London', [], {'is_remote': True, 'work_arrangement': 'onsite'}),
                ('hybrid_berlin', foreign_feed, None, 'Berlin', [], {'work_arrangement': 'hybrid'}),
                # Inactive German job
                ('inactive_munich', foreign_feed, None, 'München', [], {'is_active': False}),
                # Company / employer membership overrides
                ('bavaria_company_paris_job', bavaria_feed, None, 'Paris', [], {'title': 'Alpen Special Engineer'}),
                ('default_feed_foreign_job', default_feed, None, 'Madrid', [], {}),
                ('berlin_employer_foreign_job', foreign_feed, berlin_employer, 'Rome', [], {'title': 'Spree Special Engineer'}),
            ]

            job_ids: dict[str, int] = {}
            for key, feed, employer, loc_text, locs, extra in cases:
                job = Job(
                    feed_id=feed.id,
                    employer_company_id=employer.id if employer else None,
                    external_id=key,
                    title=extra.get('title', f'Engineer {key}'),
                    url=f'https://jobs.example/{key}',
                    location_text=loc_text,
                    is_remote=extra.get('is_remote', False),
                    work_arrangement=extra.get('work_arrangement'),
                    is_active=extra.get('is_active', True),
                )
                session.add(job)
                session.flush()
                for label, cc, lat, lon in locs:
                    session.add(JobLocation(
                        job_id=job.id,
                        label=label,
                        country_code=cc,
                        latitude=lat,
                        longitude=lon,
                    ))
                session.flush()
                job_ids[key] = job.id

            source_changes.jobs_changed(session, list(job_ids.values()))
            return {
                'jobs': job_ids,
                'foreign_co': foreign_co.id,
                'bavaria_co': bavaria_co.id,
                'berlin_employer': berlin_employer.id,
            }

    def test_precomputed_columns_match_legacy_sql_predicates_exactly(self):
        fixture = self._seed_equivalence_fixture()
        id_to_key = {jid: key for key, jid in fixture['jobs'].items()}

        with self.factory() as session:
            # 1. Germany text + country_code + bounds equivalence
            legacy_de = set(session.scalars(select(Job.id).where(or_(
                Job.locations.any(func.lower(JobLocation.country_code) == 'de'),
                _legacy_germany_text_candidates(),
            ))).all())
            compact_de = set(session.scalars(select(Job.id).where(
                search_snapshots._job_germany_fallback_candidates()
            )).all())
            precomputed_de = set(session.scalars(select(Job.id).where(
                Job.location_scope_version == LOCATION_SCOPE_VERSION,
                Job.germany_candidate.is_(True),
            )).all())
            self.assertEqual(
                {id_to_key[i] for i in precomputed_de},
                {id_to_key[i] for i in legacy_de},
            )
            self.assertEqual(
                {id_to_key[i] for i in compact_de},
                {id_to_key[i] for i in legacy_de},
            )
            # Sanity-check tricky edge cases specifically.
            matched_keys = {id_to_key[i] for i in precomputed_de}
            for must_match in (
                'city_munich_umlaut', 'city_munich_upper', 'city_muenchen_ascii',
                'city_koeln_upper', 'city_giessen_ss', 'city_dessau_ss',
                'city_multiline_munich', 'de_exact', 'de_lower', 'de_comma_suffix',
                'de_parens', 'de_prefix_dash', 'de_prefix_space_dash', 'de_slash_suffix',
                'de_slash_prefix', 'cc_de_upper', 'cc_de_lower', 'coords_bayern',
                'coords_de_edge', 'multi_foreign_and_hamburg', 'trap_zurich_in_de_bbox',
            ):
                self.assertIn(must_match, matched_keys, must_match)
            for must_not_match in (
                'trap_denver', 'trap_delaware', 'trap_code', 'trap_adecco',
                'trap_de_trailing_newline', 'trap_bordeaux', 'trap_paris',
                'trap_milan', 'trap_vienna', 'trap_empty', 'trap_none',
                'de_d_dash', 'de_d_space_dash', 'de_comma_period',
                'cc_fr_only', 'coords_outside_london', 'coords_partial_lat_only',
            ):
                self.assertNotIn(must_not_match, matched_keys, must_not_match)

            # 2. All 16 German states exact equivalence
            for state in GERMAN_STATES:
                legacy_state = set(session.scalars(select(Job.id).where(
                    _legacy_state_text_candidates(state)
                )).all())
                compact_state = set(session.scalars(select(Job.id).where(
                    search_snapshots._job_state_text_candidates(state)
                )).all())
                precomputed_state = set(session.scalars(select(Job.id).where(
                    Job.location_scope_version == LOCATION_SCOPE_VERSION,
                    Job.location_states.contains([state]),
                )).all())
                self.assertEqual(
                    {id_to_key[i] for i in precomputed_state},
                    {id_to_key[i] for i in legacy_state},
                    f'precomputed mismatch for state {state}',
                )
                self.assertEqual(
                    {id_to_key[i] for i in compact_state},
                    {id_to_key[i] for i in legacy_state},
                    f'compact fallback mismatch for state {state}',
                )

            # 3. Full scoped query equivalence across filter combinations
            combos = [
                dict(employer_country='DE', state=None, location_scope='area_remote', company_id=None, normalized_query=None, work_style='all', posting_status='open'),
                dict(employer_country='DE', state=None, location_scope='area', company_id=None, normalized_query=None, work_style='all', posting_status='open'),
                dict(employer_country='DE', state=None, location_scope='area_remote', company_id=None, normalized_query=None, work_style='remote', posting_status='open'),
                dict(employer_country='DE', state=None, location_scope='area_remote', company_id=None, normalized_query=None, work_style='hybrid', posting_status='open'),
                dict(employer_country='DE', state=None, location_scope='area_remote', company_id=None, normalized_query=None, work_style='onsite', posting_status='open'),
                dict(employer_country='DE', state=None, location_scope='area_remote', company_id=None, normalized_query=None, work_style='all', posting_status='closed'),
                dict(employer_country='DE', state=None, location_scope='area_remote', company_id=None, normalized_query=None, work_style='all', posting_status='all'),
                dict(employer_country='DE', state=None, location_scope='area_remote', company_id=fixture['bavaria_co'], normalized_query=None, work_style='all', posting_status='open'),
                dict(employer_country='DE', state=None, location_scope='area_remote', company_id=fixture['berlin_employer'], normalized_query='spree', work_style='all', posting_status='open'),
                dict(employer_country=None, state='Bayern', location_scope='area_remote', company_id=None, normalized_query=None, work_style='all', posting_status='open'),
                dict(employer_country=None, state='Bayern', location_scope='area', company_id=None, normalized_query=None, work_style='all', posting_status='open'),
                dict(employer_country=None, state='Berlin', location_scope='area_remote', company_id=None, normalized_query=None, work_style='hybrid', posting_status='open'),
                dict(employer_country=None, state='Berlin', location_scope='area_remote', company_id=None, normalized_query=None, work_style='remote', posting_status='open'),
                dict(employer_country=None, state='Schleswig-Holstein', location_scope='area', company_id=None, normalized_query=None, work_style='all', posting_status='open'),
            ]
            for params in combos:
                legacy_rows = session.execute(_legacy_scoped_job_rows_statement(**params)).all()
                new_rows = session.execute(search_snapshots._scoped_job_rows_statement(**params)).all()
                self.assertEqual(new_rows, legacy_rows, f'mismatch for {params}')

    def test_null_and_stale_versions_fallback_gracefully_and_backfill_in_batches(self):
        fixture = self._seed_equivalence_fixture()
        all_ids = sorted(fixture['jobs'].values())

        with self.factory() as session:
            expected_de = session.execute(search_snapshots._scoped_job_rows_statement(
                employer_country='DE', state=None, location_scope='area_remote',
                company_id=None, normalized_query=None, work_style='all', posting_status='open',
            )).all()
            expected_bayern = session.execute(search_snapshots._scoped_job_rows_statement(
                employer_country=None, state='Bayern', location_scope='area_remote',
                company_id=None, normalized_query=None, work_style='all', posting_status='open',
            )).all()

        # Deliberately corrupt precomputed values on NULL and stale rows to ensure
        # the query falls back to SQL predicates and ignores stale values.
        null_ids = all_ids[:10]
        stale_ids = all_ids[10:18]
        with self.factory.begin() as session:
            session.execute(
                update(Job).where(Job.id.in_(null_ids)).values(
                    germany_candidate=None,
                    location_states=None,
                    location_scope_version=None,
                )
            )
            session.execute(
                update(Job).where(Job.id.in_(stale_ids)).values(
                    germany_candidate=False,
                    location_states=['Saarland'],
                    location_scope_version='loc-scope-v0-stale',
                )
            )
            session.execute(text('DELETE FROM semantic_search_refreshes'))

        with self.factory() as session:
            self.assertEqual(
                session.execute(search_snapshots._scoped_job_rows_statement(
                    employer_country='DE', state=None, location_scope='area_remote',
                    company_id=None, normalized_query=None, work_style='all', posting_status='open',
                )).all(),
                expected_de,
            )
            self.assertEqual(
                session.execute(search_snapshots._scoped_job_rows_statement(
                    employer_country=None, state='Bayern', location_scope='area_remote',
                    company_id=None, normalized_query=None, work_style='all', posting_status='open',
                )).all(),
                expected_bayern,
            )
            # Also verify semantic_jobs_page helpers honor fallback.
            fallback_de_ids = set(session.scalars(select(Job.id).where(
                search_snapshots._job_germany_precomputed_or_fallback()
            )).all())
            legacy_de_ids = set(session.scalars(select(Job.id).where(or_(
                Job.locations.any(func.lower(JobLocation.country_code) == 'de'),
                _legacy_germany_text_candidates(),
            ))).all())
            self.assertEqual(fallback_de_ids, legacy_de_ids)
            fallback_bayern_ids = set(session.scalars(select(Job.id).where(
                search_snapshots._job_state_precomputed_or_fallback('Bayern')
            )).all())
            legacy_bayern_ids = set(session.scalars(select(Job.id).where(
                _legacy_state_text_candidates('Bayern')
            )).all())
            self.assertEqual(fallback_bayern_ids, legacy_bayern_ids)

        # Backfill in bounded batches of 5.
        batch_counts = []
        while True:
            updated = backfill_job_location_scopes_once(batch_size=5, session_factory=self.factory)
            batch_counts.append(updated)
            if updated == 0:
                break
        self.assertEqual(sum(batch_counts), len(null_ids) + len(stale_ids))
        self.assertTrue(all(count <= 5 for count in batch_counts))

        with self.factory() as session:
            stale_remaining = session.scalar(select(func.count(Job.id)).where(
                search_snapshots._job_location_scope_stale_filter()
            ))
            self.assertEqual(stale_remaining, 0)
            # Updating precomputed scope columns must NOT enqueue semantic refreshes.
            self.assertEqual(session.scalar(select(func.count(SemanticSearchRefresh.entity_id))), 0)

    def test_version_bump_triggers_fallback_and_worker_recompute(self):
        self._seed_equivalence_fixture()
        with self.factory() as session:
            baseline = session.execute(search_snapshots._scoped_job_rows_statement(
                employer_country='DE', state=None, location_scope='area_remote',
                company_id=None, normalized_query=None, work_style='all', posting_status='open',
            )).all()

        bumped = 'loc-scope-v99-test'
        with patch.object(job_location_scope, 'LOCATION_SCOPE_VERSION', bumped):
            with self.factory() as session:
                self.assertEqual(
                    session.execute(search_snapshots._scoped_job_rows_statement(
                        employer_country='DE', state=None, location_scope='area_remote',
                        company_id=None, normalized_query=None, work_style='all', posting_status='open',
                    )).all(),
                    baseline,
                )
            total = 0
            while updated := backfill_job_location_scopes_once(batch_size=25, session_factory=self.factory):
                total += updated
            self.assertGreater(total, 0)
            with self.factory() as session:
                versions = set(session.scalars(select(Job.location_scope_version)).all())
                self.assertEqual(versions, {bumped})

    def test_write_paths_keep_precomputed_scope_current(self):
        with self.factory.begin() as session:
            company = Company(source='test', source_id='wp', name='WritePath Corp', country_code='FR')
            session.add(company)
            session.flush()
            feed = JobFeed(company_id=company.id, provider='rss', feed_url='https://wp.example/feed', status='parsed')
            session.add(feed)
            session.flush()
            # Core insert path (as used by companion.py)
            job_id = session.execute(
                insert(Job).values(
                    feed_id=feed.id,
                    external_id='wp-1',
                    title='Engineer',
                    url='https://wp.example/1',
                    location_text='Paris',
                    is_remote=False,
                    raw_metadata={},
                    enrichment={},
                    is_active=True,
                    missing_complete_scans=0,
                ).returning(Job.id)
            ).scalar_one()
            source_changes.jobs_changed(session, [job_id])

        with self.factory() as session:
            row = session.execute(select(
                Job.germany_candidate, Job.location_states, Job.location_scope_version
            ).where(Job.id == job_id)).one()
            self.assertEqual(row, (False, [], LOCATION_SCOPE_VERSION))

        # Update location_text + JobLocation -> becomes German (Bayern + Berlin)
        with self.factory.begin() as session:
            job = session.get(Job, job_id)
            job.location_text = 'München'
            replace_job_locations(job, [
                {'label': 'Berlin Office', 'country_code': 'DE', 'latitude': 52.52, 'longitude': 13.405},
            ])
            source_changes.jobs_changed(session, [job.id])

        with self.factory() as session:
            germany, states, version = session.execute(select(
                Job.germany_candidate, Job.location_states, Job.location_scope_version
            ).where(Job.id == job_id)).one()
            self.assertTrue(germany)
            self.assertIn('Bayern', states)
            self.assertIn('Berlin', states)
            self.assertEqual(version, LOCATION_SCOPE_VERSION)

        # Move job back outside Germany -> cleared immediately
        with self.factory.begin() as session:
            job = session.get(Job, job_id)
            job.location_text = 'Lyon, France'
            replace_job_locations(job, [
                {'label': 'Lyon', 'country_code': 'FR', 'latitude': 45.764, 'longitude': 4.8357},
            ])
            source_changes.jobs_changed(session, [job.id])

        with self.factory() as session:
            row = session.execute(select(
                Job.germany_candidate, Job.location_states, Job.location_scope_version
            ).where(Job.id == job_id)).one()
            self.assertEqual(row, (False, [], LOCATION_SCOPE_VERSION))

    def test_materialize_search_snapshot_runs_single_scope_query(self):
        self._seed_equivalence_fixture()
        with self.factory.begin() as session:
            profile = UserProfile(name='Searcher', preferences={
                'desired_roles': ['Engineer'], 'skills': ['Python'], 'summary': 'Builds services.',
            })
            session.add(profile)
            session.flush()
            profile_id = profile.id

        for criteria_patch in (
            {'employer_country': 'DE', 'state': None, 'country': 'DE'},
            {'employer_country': None, 'state': 'Bayern', 'country': 'DE'},
        ):
            criteria = {
                'latitude': None,
                'longitude': None,
                'radius_km': None,
                'place': None,
                'location_scope': 'area_remote',
                'company_id': None,
                'query': None,
                'work_style': 'all',
                'posting_status': 'open',
                'freshness_bucket': 1,
                **criteria_patch,
            }
            statements: list[str] = []

            def record(_conn, _cursor, statement, _params, _context, many):
                if not many and statement.lstrip().upper().startswith('SELECT'):
                    statements.append(' '.join(statement.split()))

            event.listen(self.engine, 'before_cursor_execute', record)
            try:
                with self.factory() as session:
                    profile = session.get(UserProfile, profile_id)
                    snapshot = search_snapshots.materialize_search_snapshot(
                        session,
                        [profile],
                        key=f'test-scope-{criteria_patch["employer_country"] or criteria_patch["state"]}',
                        generation=1,
                        criteria=criteria,
                        thresholds={profile_id: (0, False)},
                    )
            finally:
                event.remove(self.engine, 'before_cursor_execute', record)

            # No separate count(job_id) / count(distinct feed_id) query should execute.
            count_queries = [stmt for stmt in statements if 'count(distinct' in stmt.lower() and 'feed_id' in stmt.lower()]
            self.assertEqual(count_queries, [], f'duplicate source count query ran: {count_queries}')
            union_scope_queries = [stmt for stmt in statements if ' UNION ' in stmt and 'location_scope_version' in stmt]
            self.assertEqual(len(union_scope_queries), 1, f'expected 1 scope query, got {len(union_scope_queries)}')
            self.assertGreater(snapshot.payload['counts']['source'], 0)
            self.assertGreater(snapshot.payload['coverage']['source_count'], 0)


if __name__ == '__main__':
    unittest.main()
