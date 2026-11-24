"""Equivalence, fallback, backfill, concurrency, and trigger invalidation tests for job location scopes."""
from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sqlalchemy import and_, delete, event, func, insert, or_, select, text, update
from sqlalchemy.orm import aliased, sessionmaker

from hiring_scraper.app import job_location_scope, models, search_snapshots, source_changes
from hiring_scraper.app.board_scope import german_state_bounds
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
    JobLocationScopeDirty,
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
    """Pre-refactor candidate/source query for Germany and state scopes, minus the
    retired employer-country/state fallback (scope now follows the work location only)."""
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
            Job.locations.any(func.lower(JobLocation.country_code) == "de"),
            _legacy_germany_text_candidates(),
        ]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            clauses.append(search_snapshots._remote_job_candidate())
        filters.append(or_(*clauses))
    elif state:
        clauses = [
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

    def _drain_backfill(self, batch_size: int = 500) -> int:
        total = 0
        while updated := backfill_job_location_scopes_once(batch_size=batch_size, session_factory=self.factory):
            total += updated
        return total

    def _seed_equivalence_fixture(self) -> dict[str, int]:
        """Seed a varied corpus covering PG C.UTF-8 ILIKE edge cases, DE forms, coords, remote, and traps."""
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
                # German cities, umlauts, uppercase, transliterations, eszett / capital eszett / Turkish dotted I
                ('city_munich_umlaut', foreign_feed, None, 'München', [], {}),
                ('city_munich_upper', foreign_feed, None, 'MÜNCHEN, Office', [], {}),
                ('city_muenchen_ascii', foreign_feed, None, 'Muenchen / Hybrid', [], {'work_arrangement': 'hybrid'}),
                ('city_muenchen_upper_text', foreign_feed, None, 'MUENCHEN', [], {}),
                ('city_munich_umlaut_label', foreign_feed, None, 'Central Europe', [('München Campus', None, None, None)], {}),
                ('city_muenchen_upper_label', foreign_feed, None, 'Central Europe', [('MUENCHEN Hub', None, None, None)], {}),
                ('city_berlin_turkish_i_text', foreign_feed, None, 'BERL\u0130N', [], {}),
                ('city_berlin_turkish_i_label', foreign_feed, None, 'EMEA', [('BERL\u0130N Campus', None, None, None)], {}),
                ('city_strasse_eszett_text', foreign_feed, None, 'STRAßE, BERL\u0130N', [], {}),
                ('city_strasse_cap_eszett_text', foreign_feed, None, 'STRA\u1e9eE, Paris', [], {}),
                ('city_strasse_eszett_label', foreign_feed, None, 'EMEA', [('STRAßE, BERL\u0130N', None, None, None)], {}),
                ('city_strasse_cap_eszett_label', foreign_feed, None, 'EMEA', [('STRA\u1e9eE, Paris', None, None, None)], {}),
                ('city_giessen_cap_eszett_text', foreign_feed, None, 'GIE\u1e9eEN', [], {}),
                ('city_giessen_cap_eszett_label', foreign_feed, None, 'EMEA', [('GIE\u1e9eEN Office', None, None, None)], {}),
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
                # Literal SQL LIKE special characters (_, %, \) in location_text and JobLocation.label
                ('special_chars_berlin_text', foreign_feed, None, r'100%_Berlin\Office', [], {}),
                ('special_chars_berlin_label', foreign_feed, None, 'EMEA', [(r'100%_Berlin\Office', None, None, None)], {}),
                ('special_chars_paris_text', foreign_feed, None, r'Paris_100%\HQ', [], {}),
                ('special_chars_paris_label', foreign_feed, None, 'EMEA', [(r'Paris_100%\HQ', None, None, None)], {}),
                ('special_chars_de_backslash_text', foreign_feed, None, r'Branch\, DE', [], {}),
                ('special_chars_de_backslash_label', foreign_feed, None, 'EMEA', [(r'Branch\, DE', None, None, None)], {}),
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

        self._drain_backfill()
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
            # Sanity-check tricky Unicode (Turkish dotted I, eszett, wildcards/backslash) and DE forms.
            matched_keys = {id_to_key[i] for i in precomputed_de}
            for must_match in (
                'city_munich_umlaut', 'city_munich_upper', 'city_muenchen_ascii',
                'city_muenchen_upper_text', 'city_munich_umlaut_label', 'city_muenchen_upper_label',
                'city_berlin_turkish_i_text', 'city_berlin_turkish_i_label',
                'city_strasse_eszett_text', 'city_strasse_eszett_label',
                'special_chars_berlin_text', 'special_chars_berlin_label',
                'special_chars_de_backslash_text', 'special_chars_de_backslash_label',
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
                'special_chars_paris_text', 'special_chars_paris_label',
                'city_strasse_cap_eszett_text', 'city_strasse_cap_eszett_label',
                'de_d_dash', 'de_d_space_dash', 'de_comma_period',
                'cc_fr_only', 'coords_outside_london', 'coords_partial_lat_only',
            ):
                self.assertNotIn(must_not_match, matched_keys, must_not_match)

            # Specifically verify BERLİN matches Berlin state in both location_text and JobLocation.label.
            berlin_keys = {
                id_to_key[i]
                for i in session.scalars(select(Job.id).where(
                    Job.location_scope_version == LOCATION_SCOPE_VERSION,
                    Job.location_states.contains(['Berlin']),
                )).all()
            }
            self.assertIn('city_berlin_turkish_i_text', berlin_keys)
            self.assertIn('city_berlin_turkish_i_label', berlin_keys)
            self.assertIn('special_chars_berlin_text', berlin_keys)
            self.assertIn('special_chars_berlin_label', berlin_keys)

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

        # Deliberately corrupt precomputed values on NULL and older v1 rows to ensure
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
                    location_scope_version='loc-scope-v1-stale',
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
            total = self._drain_backfill(batch_size=25)
            self.assertGreater(total, 0)
            with self.factory() as session:
                versions = set(session.scalars(select(Job.location_scope_version)).all())
                self.assertEqual(versions, {bumped})

    def test_write_paths_and_mixed_orm_core_writes_invalidate_via_triggers(self):
        with self.factory.begin() as session:
            company = Company(source='test', source_id='wp', name='WritePath Corp', country_code='FR')
            session.add(company)
            session.flush()
            feed = JobFeed(company_id=company.id, provider='rss', feed_url='https://wp.example/feed', status='parsed')
            session.add(feed)
            session.flush()
            # Core insert starts with location_scope_version = NULL; worker computes in SQL.
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
        with self.factory() as session:
            self.assertIsNone(session.scalar(select(Job.location_scope_version).where(Job.id == job_id)))
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory() as session:
            row = session.execute(select(
                Job.germany_candidate, Job.location_states, Job.location_scope_version
            ).where(Job.id == job_id)).one()
            self.assertEqual(row, (False, [], LOCATION_SCOPE_VERSION))

        # Non-location ORM update must keep the stamped scope version intact.
        with self.factory.begin() as session:
            job = session.get(Job, job_id)
            job.title = 'Senior Engineer'
            source_changes.jobs_changed(session, [job.id])
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(Job.location_scope_version).where(Job.id == job_id)),
                LOCATION_SCOPE_VERSION,
            )

        # Mixed ORM title update + Core UPDATE of location_text -> trigger invalidates
        # location_scope_version even without ORM history on location_text.
        with self.factory.begin() as session:
            job = session.get(Job, job_id)
            job.title = 'Lead Engineer'
            session.flush()
            session.execute(update(Job).where(Job.id == job_id).values(location_text='BERL\u0130N'))
            source_changes.jobs_changed(session, [job_id])

        with self.factory() as session:
            self.assertIsNone(session.scalar(select(Job.location_scope_version).where(Job.id == job_id)))
            # Immediate visibility via indexed NULL fallback before worker runs.
            berlin_ids = {
                row[0] for row in session.execute(search_snapshots._scoped_job_rows_statement(
                    employer_country=None, state='Berlin', location_scope='area_remote',
                    company_id=None, normalized_query=None, work_style='all', posting_status='open',
                )).all()
            }
            self.assertIn(job_id, berlin_ids)

        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory() as session:
            row = session.execute(select(
                Job.germany_candidate, Job.location_states, Job.location_scope_version
            ).where(Job.id == job_id)).one()
            self.assertEqual(row, (True, ['Berlin'], LOCATION_SCOPE_VERSION))

        # ORM location_text + replace_job_locations -> Bayern + Berlin after backfill.
        with self.factory.begin() as session:
            job = session.get(Job, job_id)
            job.location_text = 'München'
            replace_job_locations(job, [
                {'label': 'Berlin Office', 'country_code': 'DE', 'latitude': 52.52, 'longitude': 13.405},
            ])
            source_changes.jobs_changed(session, [job.id])

        with self.factory() as session:
            self.assertIsNone(session.scalar(select(Job.location_scope_version).where(Job.id == job_id)))
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory() as session:
            germany, states, version = session.execute(select(
                Job.germany_candidate, Job.location_states, Job.location_scope_version
            ).where(Job.id == job_id)).one()
            self.assertTrue(germany)
            self.assertEqual(states, ['Bayern', 'Berlin', 'Brandenburg'])
            self.assertEqual(version, LOCATION_SCOPE_VERSION)

        # Move job outside Germany -> cleared immediately via trigger + backfill.
        with self.factory.begin() as session:
            job = session.get(Job, job_id)
            job.location_text = 'Lyon, France'
            replace_job_locations(job, [
                {'label': 'Lyon', 'country_code': 'FR', 'latitude': 45.764, 'longitude': 4.8357},
            ])
            source_changes.jobs_changed(session, [job.id])
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory() as session:
            self.assertEqual(
                session.execute(select(
                    Job.germany_candidate, Job.location_states, Job.location_scope_version
                ).where(Job.id == job_id)).one(),
                (False, [], LOCATION_SCOPE_VERSION),
            )

        # Mixed ORM title update + Core INSERT / UPDATE / DELETE on job_locations.
        with self.factory.begin() as session:
            job = session.get(Job, job_id)
            job.title = 'Staff Engineer'
            session.flush()
            loc_id = session.execute(
                insert(JobLocation).values(job_id=job_id, label='München Hub').returning(JobLocation.id)
            ).scalar_one()
            source_changes.jobs_changed(session, [job_id])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Job.location_scope_version).where(Job.id == job_id)),
                             LOCATION_SCOPE_VERSION)
            self.assertEqual(session.scalar(select(JobLocationScopeDirty.job_id)), job_id)
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory() as session:
            self.assertEqual(
                session.execute(select(
                    Job.germany_candidate, Job.location_states, Job.location_scope_version
                ).where(Job.id == job_id)).one(),
                (True, ['Bayern'], LOCATION_SCOPE_VERSION),
            )

        # Updating only a non-scope JobLocation column (precision) keeps the version intact.
        with self.factory.begin() as session:
            session.execute(update(JobLocation).where(JobLocation.id == loc_id).values(precision='city'))
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(Job.location_scope_version).where(Job.id == job_id)),
                LOCATION_SCOPE_VERSION,
            )

            self.assertIsNone(session.scalar(select(JobLocationScopeDirty.job_id)))

        # Core UPDATE of JobLocation.label invalidates and recomputes to Hamburg.
        with self.factory.begin() as session:
            session.execute(update(JobLocation).where(JobLocation.id == loc_id).values(label='Hamburg Port'))
            source_changes.jobs_changed(session, [job_id])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Job.location_scope_version).where(Job.id == job_id)),
                             LOCATION_SCOPE_VERSION)
            self.assertEqual(session.scalar(select(JobLocationScopeDirty.job_id)), job_id)
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory() as session:
            self.assertEqual(
                session.execute(select(
                    Job.germany_candidate, Job.location_states, Job.location_scope_version
                ).where(Job.id == job_id)).one(),
                (True, ['Hamburg'], LOCATION_SCOPE_VERSION),
            )

        # Core DELETE of JobLocation invalidates and recomputes back to non-German.
        with self.factory.begin() as session:
            session.execute(delete(JobLocation).where(JobLocation.id == loc_id))
            source_changes.jobs_changed(session, [job_id])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Job.location_scope_version).where(Job.id == job_id)),
                             LOCATION_SCOPE_VERSION)
            self.assertEqual(session.scalar(select(JobLocationScopeDirty.job_id)), job_id)
            for country, state, predicate in (
                ('DE', None, search_snapshots._job_germany_precomputed_or_fallback()),
                (None, 'Hamburg', search_snapshots._job_state_precomputed_or_fallback('Hamburg')),
            ):
                self.assertIsNone(session.scalar(select(Job.id).where(Job.id == job_id, predicate)))
                self.assertNotIn(job_id, [row.id for row in session.execute(
                    search_snapshots._scoped_job_rows_statement(
                        employer_country=country, state=state, location_scope='area',
                        company_id=None, normalized_query=None, work_style='all', posting_status='open',
                    )
                )])
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory() as session:
            self.assertEqual(
                session.execute(select(
                    Job.germany_candidate, Job.location_states, Job.location_scope_version
                ).where(Job.id == job_id)).one(),
                (False, [], LOCATION_SCOPE_VERSION),
            )

    def test_concurrent_writer_during_backfill_never_leaves_stale_current_scope(self):
        with self.factory.begin() as session:
            company = Company(source='test', source_id='race', name='Race Corp', country_code='FR')
            session.add(company)
            session.flush()
            feed = JobFeed(company_id=company.id, provider='rss', feed_url='https://race.example/feed', status='parsed')
            session.add(feed)
            session.flush()
            jobs = []
            for key in ('writer-first', 'unlocked-peer', 'backfill-first-text', 'backfill-first-loc'):
                job = Job(
                    feed_id=feed.id, external_id=key, title=f'Role {key}',
                    url=f'https://race.example/{key}', location_text='Paris',
                )
                session.add(job)
                session.flush()
                jobs.append((key, job.id))
        job_ids = dict(jobs)

        # Case 1: concurrent writer already holds a row lock on writer-first while backfill runs.
        # Backfill must SKIP LOCKED writer-first, update unlocked-peer, and never overwrite writer-first.
        writer_locked = threading.Event()
        release_writer = threading.Event()
        writer_errors: list[Exception] = []

        def hold_and_update_first():
            try:
                with self.factory.begin() as session:
                    session.execute(
                        update(Job).where(Job.id == job_ids['writer-first']).values(location_text='Berlin')
                    )
                    writer_locked.set()
                    if not release_writer.wait(6):
                        raise AssertionError('writer did not receive release signal')
            except Exception as error:
                writer_errors.append(error)

        thread = threading.Thread(target=hold_and_update_first, name='writer-first')
        thread.start()
        try:
            self.assertTrue(writer_locked.wait(3))
            # Limit batch_size=2 so candidate_ids are [writer-first, unlocked-peer].
            updated = backfill_job_location_scopes_once(batch_size=2, session_factory=self.factory)
            self.assertEqual(updated, 1)
            release_writer.set()
            thread.join(6)
        finally:
            release_writer.set()
            thread.join(6)
        self.assertEqual(writer_errors, [])
        with self.factory() as session:
            # unlocked-peer is current (Paris); writer-first is NULL (Berlin), NOT Paris+current.
            self.assertEqual(
                session.execute(select(
                    Job.germany_candidate, Job.location_states, Job.location_scope_version
                ).where(Job.id == job_ids['unlocked-peer'])).one(),
                (False, [], LOCATION_SCOPE_VERSION),
            )
            self.assertIsNone(session.scalar(
                select(Job.location_scope_version).where(Job.id == job_ids['writer-first'])
            ))
        # Drain writer-first so remaining NULL rows are backfill-first-text and backfill-first-loc.
        self.assertEqual(backfill_job_location_scopes_once(batch_size=1, session_factory=self.factory), 1)
        with self.factory() as session:
            self.assertEqual(
                session.execute(select(
                    Job.germany_candidate, Job.location_states, Job.location_scope_version
                ).where(Job.id == job_ids['writer-first'])).one(),
                (True, ['Berlin'], LOCATION_SCOPE_VERSION),
            )

        # Text changes serialize on the parent lock and clear the version.
        # Child edits can commit before the backfill does. Pause after its SQL
        # statement to verify that a new edit survives the deletion of older marks.
        for target_key, writer_action in (
            (
                'backfill-first-text',
                lambda s, jid: s.execute(update(Job).where(Job.id == jid).values(location_text='Berlin')),
            ),
            (
                'backfill-first-loc',
                lambda s, jid: s.execute(
                    update(JobLocation).where(JobLocation.job_id == jid).values(label='Berlin', country_code='DE')
                ),
            ),
        ):
            target_id = job_ids[target_key]
            edits_child = target_key == 'backfill-first-loc'
            if edits_child:
                with self.factory.begin() as session:
                    session.add(JobLocation(job_id=target_id, label='Paris', country_code='FR'))
            backfill_locked = threading.Event()
            release_backfill = threading.Event()
            writer_started = threading.Event()
            writer_finished = threading.Event()
            errors: list[Exception] = []

            def pause_after_lock(_conn, _cursor, statement, *_args):
                normalized = ' '.join(statement.upper().split())
                pause_at = 'UPDATE JOBS AS J' if edits_child else 'FOR NO KEY UPDATE SKIP LOCKED'
                if threading.current_thread().name == f'backfill-{target_key}' and pause_at in normalized:
                    backfill_locked.set()
                    if not release_backfill.wait(6):
                        raise AssertionError('backfill did not receive release signal')

            def run_backfill():
                try:
                    backfill_job_location_scopes_once(batch_size=1, session_factory=self.factory)
                except Exception as error:
                    errors.append(error)

            def run_writer():
                try:
                    with self.factory.begin() as session:
                        writer_started.set()
                        writer_action(session, target_id)
                except Exception as error:
                    errors.append(error)
                finally:
                    writer_finished.set()

            event.listen(self.engine, 'after_cursor_execute', pause_after_lock)
            bf_thread = threading.Thread(target=run_backfill, name=f'backfill-{target_key}')
            wr_thread = threading.Thread(target=run_writer, name=f'writer-{target_key}')
            try:
                bf_thread.start()
                self.assertTrue(backfill_locked.wait(3), f'{target_key}: backfill did not acquire lock')
                wr_thread.start()
                self.assertTrue(writer_started.wait(3), f'{target_key}: writer did not start')
                if edits_child:
                    self.assertTrue(writer_finished.wait(3), 'child writer waited for backfill')
                else:
                    self.assertFalse(writer_finished.wait(0.25), 'text writer bypassed backfill lock')
                release_backfill.set()
                bf_thread.join(6)
                wr_thread.join(6)
            finally:
                release_backfill.set()
                bf_thread.join(6)
                if wr_thread.ident is not None:
                    wr_thread.join(6)
                event.remove(self.engine, 'after_cursor_execute', pause_after_lock)

            self.assertEqual(errors, [], f'{target_key} concurrent errors: {errors}')
            with self.factory() as session:
                if edits_child:
                    self.assertEqual(session.scalars(select(JobLocationScopeDirty.job_id)).all(), [target_id])
                    self.assertEqual(session.scalar(select(Job.id).where(
                        Job.id == target_id, search_snapshots._job_state_precomputed_or_fallback('Berlin'),
                    )), target_id)
                else:
                    self.assertIsNone(session.scalar(select(Job.location_scope_version).where(Job.id == target_id)))
                berlin_ids = {
                    row[0] for row in session.execute(search_snapshots._scoped_job_rows_statement(
                        employer_country=None, state='Berlin', location_scope='area_remote',
                        company_id=None, normalized_query=None, work_style='all', posting_status='open',
                    )).all()
                }
                self.assertIn(target_id, berlin_ids, f'{target_key}: fallback did not find updated Berlin job')

            self.assertEqual(backfill_job_location_scopes_once(batch_size=1, session_factory=self.factory), 1)
            with self.factory() as session:
                self.assertEqual(
                    session.execute(select(
                        Job.germany_candidate, Job.location_states, Job.location_scope_version
                    ).where(Job.id == target_id)).one(),
                    (True, ['Berlin'], LOCATION_SCOPE_VERSION),
                )

    def _seed_lock_job(self):
        with self.factory.begin() as session:
            job = Job(
                feed=JobFeed(
                    company=Company(source='test', source_id='lock-job', name='Employer'),
                    provider='rss', feed_url='https://lock.example/feed', status='parsed',
                ),
                external_id='role', title='Engineer', url='https://lock.example/job',
                locations=[JobLocation(label='Paris'), JobLocation(label='Lyon')],
            )
            session.add(job)
            session.flush()
            ids = job.feed_id, job.id, job.locations[0].id
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        return ids

    def test_core_label_update_after_title_flush_does_not_wait_for_feed_editor(self):
        feed_id, job_id, loc_id = self._seed_lock_job()

        def edit_feed():
            with self.factory.begin() as editor:
                editor.scalar(select(JobFeed.id).where(JobFeed.id == feed_id).with_for_update())
                models.reserve_job_source_rows(editor, [job_id])

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.factory.begin() as writer, self.engine.connect() as observer:
                writer.execute(text("SET LOCAL lock_timeout = '1s'"))
                job = writer.get(Job, job_id)
                job.title = 'Senior Engineer'
                writer.flush()
                pid = writer.scalar(text('SELECT pg_backend_pid()'))
                editor = pool.submit(edit_feed)
                pg.wait_for_blocker(observer, pid)
                writer.execute(update(JobLocation).where(JobLocation.id == loc_id).values(label='Berlin'))
                source_changes.jobs_changed(writer, [job_id])
            editor.result(timeout=5)
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Job.location_states).where(Job.id == job_id)), ['Berlin'])

    def test_direct_label_edit_does_not_wait_for_ingestion_replacing_the_location(self):
        _, job_id, loc_id = self._seed_lock_job()

        def ingest():
            with self.factory.begin() as session:
                models.reserve_job_source_rows(session, [job_id])
                replace_job_locations(session.get(Job, job_id), [{'label': 'München'}])
                source_changes.jobs_changed(session, [job_id])

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.factory.begin() as writer, self.engine.connect() as observer:
                writer.execute(text("SET LOCAL lock_timeout = '1s'"))
                writer.scalar(select(JobLocation.id).where(JobLocation.id == loc_id).with_for_update())
                pid = writer.scalar(text('SELECT pg_backend_pid()'))
                ingestion = pool.submit(ingest)
                pg.wait_for_blocker(observer, pid)
                writer.execute(update(JobLocation).where(JobLocation.id == loc_id).values(label='Berlin'))
            ingestion.result(timeout=5)
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Job.location_states).where(Job.id == job_id)), ['Bayern'])

    def test_backfill_discards_dirty_marks_for_deleted_jobs(self):
        _, job_id, _ = self._seed_lock_job()
        with self.factory.begin() as session:
            session.execute(delete(Job).where(Job.id == job_id))
        with self.factory() as session:
            self.assertEqual(session.scalars(select(JobLocationScopeDirty.job_id)).all(), [job_id])
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 0)
        with self.factory() as session:
            self.assertIsNone(session.scalar(select(JobLocationScopeDirty.job_id)))

    def test_location_reorder_preserves_scope_and_rows(self):
        _, job_id, _ = self._seed_lock_job()
        with self.factory.begin() as session:
            job = session.get(Job, job_id)
            location_ids = [loc.id for loc in job.locations]
            replace_job_locations(job, [{'label': loc.label} for loc in reversed(job.locations)])
            session.flush()
            self.assertEqual([loc.id for loc in job.locations], location_ids)
            self.assertEqual(session.scalar(select(Job.location_scope_version).where(Job.id == job_id)),
                             LOCATION_SCOPE_VERSION)
        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 0)

    def test_location_replace_and_backfill_do_not_wait_for_feed_key_share(self):
        with self.factory.begin() as session:
            feed = JobFeed(
                company=Company(source='test', source_id='feed-lock', name='Employer'),
                provider='rss', feed_url='https://example.test/feed', status='parsed',
            )
            job = Job(feed=feed, external_id='role', title='Engineer', url='https://example.test/job')
            job.locations = [JobLocation(label='Paris')]
            session.add(job)
            session.flush()
            feed_id, job_id = feed.id, job.id

        self.assertEqual(backfill_job_location_scopes_once(session_factory=self.factory), 1)
        with self.factory.begin() as editor:
            # Managed-feed edits take this lock before trying to lock the jobs.
            editor.scalar(select(JobFeed.id).where(JobFeed.id == feed_id).with_for_update())
            with self.factory.begin() as writer:
                writer.execute(text("SET LOCAL lock_timeout = '1s'"))
                job = writer.get(Job, job_id)
                job.title = 'Senior Engineer'
                replace_job_locations(job, [{'label': 'Berlin'}])
                source_changes.jobs_changed(writer, [job_id])

            # Replacement records a dirty mark without a second Job UPDATE.
            # Backfill recomputes the scope in its own transaction.
            with self.engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '1s'"))
                self.assertEqual(backfill_job_location_scopes_once(
                    session_factory=sessionmaker(bind=connection),
                ), 1)
                self.assertEqual(connection.execute(select(
                    Job.germany_candidate, Job.location_states, Job.location_scope_version
                ).where(Job.id == job_id)).one(), (True, ['Berlin'], LOCATION_SCOPE_VERSION))

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
