"""Persisted model assessments replace numeric matching without network reads."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import importlib
import importlib.util
import unittest
import warnings
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from hiring_scraper.app import api, models, profiles
from hiring_scraper.app.job_search import ensure_profile_match_indexes, profile_match_index_stale


PROFILE = dict(name='Developer', desired_roles=['Software developer'], skills=['Python', 'SQL'],
               languages=['English'], summary='Build Python services and SQL databases.',
               matching_defaults={'min_match_score': 30})
DESCRIPTION = 'Your responsibilities:\nBuild Python services and SQL databases. ' * 12
USAGE = {'state': 'ready', 'allowed': True, 'used_percent': 10}


def assessment(score=90):
    return {'dimensions': {name: {'score': score, 'evidence': 'Python services are documented.'}
                           for name in ['skills', 'experience', 'behavioral', 'career_alignment']},
            'gates': {name: {'status': 'pass', 'reason': 'Explicitly compatible.'}
                      for name in ['location', 'language', 'eligibility']},
            'strengths': ['Documented service experience.'], 'gaps': [], 'unknowns': []}


class AssessmentTests(unittest.TestCase):
    def service(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.profile_llm'),
                             'Persisted profile LLM assessments are not implemented')
        return importlib.import_module('hiring_scraper.app.profile_llm')

    def test_unknown_dimensions_renormalize_without_inventing_neutral_scores(self):
        service = self.service()
        data = assessment()
        data['dimensions']['skills']['score'] = 100
        data['dimensions']['experience']['score'] = 40
        data['dimensions']['behavioral']['score'] = None
        data['dimensions']['career_alignment']['score'] = None
        result = service.validate_assessment(data)
        self.assertEqual(result['score'], 73)
        self.assertTrue(result['uncertain'])
        self.assertIsNone(result['dimensions']['career_alignment']['score'])

    def test_malformed_scores_and_extra_output_are_rejected(self):
        service = self.service()
        for value in [True, '80', -1, 101, float('nan'), float('inf')]:
            data = assessment()
            data['dimensions']['skills']['score'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.validate_assessment(data)
        data = assessment(); data['score'] = 100
        with self.assertRaises(ValueError):
            service.validate_assessment(data)

    def test_failed_gate_vetoes_eligibility_unknown_gate_marks_uncertainty(self):
        service = self.service()
        data = assessment(); data['gates']['language'] = {'status': 'unknown', 'reason': 'No language level supplied.'}
        result = service.validate_assessment(data)
        self.assertTrue(result['eligible']); self.assertTrue(result['uncertain'])
        data['gates']['eligibility'] = {'status': 'fail', 'reason': 'Required authorization is absent.'}
        self.assertFalse(service.validate_assessment(data)['eligible'])


class PersistedAssessmentTests(unittest.TestCase):
    service = AssessmentTests.service

    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool,
                                    connect_args={'check_same_thread': False})
        models.Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        with self.factory() as session:
            self.profile_id = profiles.create_profile(profiles.ProfileRequest(**PROFILE), session)['id']
            company = models.Company(source='test', source_id='llm', name='Example')
            feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://example.org/jobs')
            job = models.Job(feed=feed, title='Software developer', description=DESCRIPTION,
                             external_id='one', url='https://example.org/jobs/one', is_remote=True)
            session.add(company); session.commit(); self.job_id = job.id
            ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def refresh(self, data=None, generate=None, usage=None):
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory):
            return service.refresh_llm_scores_once(
                generate=generate or (lambda prompt, schema, **kwargs: copy.deepcopy(data or assessment())),
                usage=usage or (lambda: dict(USAGE)))

    def test_persisted_high_low_and_zero_scores_replace_detail_journey_and_index(self):
        for score in [91, 12, 0]:
            with self.subTest(score=score):
                if score != 91:
                    with self.factory() as session:
                        job = session.get(models.Job, self.job_id)
                        job.description += f'\nRevision {score}.'
                        session.commit()
                        ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
                        session.commit()
                self.assertTrue(self.refresh(assessment(score)))
                with self.factory() as session:
                    detail = api.get_job(self.job_id, session=session, profile_id=self.profile_id)
                    match = detail['profile_match']
                    self.assertEqual(match['score'], score)
                    self.assertEqual(match['score_source'], 'llm')
                    self.assertEqual(match['llm_match']['model'], 'gpt-6-luna')
                    row = session.get(models.ProfileJobMatch, (self.profile_id, self.job_id))
                    self.assertEqual(row.match_score, score)
                    if score == 91:
                        page = api.list_applications(profile_ids=str(self.profile_id), statuses='all', sort='match', offset=0, limit=40, session=session)
                        self.assertEqual(page['items'][0]['profile_match']['score'], 91)
                        api.set_job_application(self.profile_id, self.job_id, api.ApplicationRequest(status='open'), session)
                    else:
                        self.assertEqual(detail['application']['status'], 'open')

    def test_unchanged_cache_never_runs_inference_and_contacts_do_not_invalidate(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            profiles.update_profile(self.profile_id, profiles.ProfileRequest(**PROFILE,
                application_details={'email': 'secret@example.org'}), session)
            self.assertFalse(profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))
        def forbidden(*args, **kwargs):
            self.fail('Unchanged assessment must not call inference')
        self.assertFalse(self.refresh(generate=forbidden))

    def test_capture_bookkeeping_does_not_change_assessment_hash_or_invalidate_cache(self):
        service = self.service()
        with self.factory.begin() as session:
            job = session.get(models.Job, self.job_id)
            job.raw_metadata = {
                'source': 'arbeitsagentur', 'source_observed_at': '2026-10-01T08:00:00Z',
                'source_api_url': 'https://api.example.test/detail/one',
                'search_terms': ['Software developer'], 'detail_state': 'captured',
                'source_record': {'referenznummer': 'one', 'stellenangebotsTitel': 'Software developer',
                                  '_checked_at': '2026-10-01T08:00:00Z',
                                  '_detail_url': 'https://api.example.test/detail/one',
                                  '_search_terms': ['Software developer']},
            }
        self.assertTrue(self.refresh(assessment(91)))
        with self.factory.begin() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            cached_hash = service.source_hash(job, profile)
            metadata = dict(job.raw_metadata)
            metadata.update(source_observed_at='2026-10-02T08:00:00Z',
                            source_api_url='https://api.example.test/detail/one?capture=2',
                            search_terms=['Software engineer'], detail_state='refreshed')
            metadata['source_record'] = {
                **metadata['source_record'], '_checked_at': '2026-10-02T08:00:00Z',
                '_detail_url': 'https://api.example.test/detail/one?capture=2',
                '_search_terms': ['Software engineer'],
            }
            job.raw_metadata = metadata

        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            self.assertEqual(service.source_hash(job, profile), cached_hash)
            self.assertIsNotNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

        def forbidden(*args, **kwargs):
            self.fail('Capture bookkeeping alone must not cause another assessment')
        self.assertFalse(self.refresh(generate=forbidden))

    def test_career_goals_invalidate_llm_without_changing_local_projection(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            before = profile.matching_preferences
            profiles.update_profile(self.profile_id, profiles.ProfileRequest(**PROFILE,
                application_personalization={'career_goals': 'Lead technical teams.'}), session)
            self.assertEqual(profile.matching_preferences, before)
            self.assertTrue(profile_match_index_stale(profile))
            detail = api.get_job(self.job_id, session=session, profile_id=self.profile_id)
            self.assertEqual(detail['profile_match']['score_source'], 'local')
        self.assertTrue(self.refresh())

    def test_explicit_zero_experience_is_prompted_hashed_and_invalidates_cached_assessment(self):
        service = self.service()
        self.assertTrue(self.refresh(assessment(91)))
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            job = session.get(models.Job, self.job_id)
            unknown_hash = service.source_hash(job, profile)
            self.assertNotIn('experience_years', service.profile_data(profile))

            profiles.update_profile(self.profile_id, profiles.ProfileRequest(
                **PROFILE, experience_years=0), session)

            profile = session.get(models.UserProfile, self.profile_id)
            job = session.get(models.Job, self.job_id)
            self.assertEqual(service.profile_data(profile)['experience_years'], 0)
            self.assertNotEqual(service.source_hash(job, profile), unknown_hash)
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
            self.assertIn('experience_years', service.build_prompt(job, profile))
            ensure_profile_match_indexes(session, [profile])
            session.commit()

        self.assertTrue(self.refresh(assessment(72)))
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            job = session.get(models.Job, self.job_id)
            zero_hash = service.source_hash(job, profile)
            self.assertEqual(profile.preferences['experience_years'], 0)

            profiles.update_profile(self.profile_id, profiles.ProfileRequest(**PROFILE), session)

            profile = session.get(models.UserProfile, self.profile_id)
            job = session.get(models.Job, self.job_id)
            self.assertNotIn('experience_years', service.profile_data(profile))
            self.assertNotEqual(service.source_hash(job, profile), zero_hash)
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
            ensure_profile_match_indexes(session, [profile])
            session.commit()

    def test_source_changes_during_inference_discard_job_or_profile_snapshot(self):
        for kind in ['job', 'profile']:
            def generate(prompt, schema, **kwargs):
                with self.factory() as session:
                    if kind == 'job':
                        session.get(models.Job, self.job_id).description += '\nChanged duties.'
                    else:
                        profile = session.get(models.UserProfile, self.profile_id)
                        profile.preferences = {**profile.preferences, 'application_personalization': {'career_goals': 'Research systems.'}}
                    session.commit()
                return assessment()
            with self.subTest(kind=kind):
                self.assertFalse(self.refresh(generate=generate))
                with self.factory() as session:
                    self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_usage_block_and_disconnection_never_start_inference(self):
        def forbidden(*args, **kwargs):
            self.fail('Blocked quota must not start inference')
        for state in [{'state': 'paused', 'allowed': False, 'used_percent': 50},
                      {'state': 'disconnected', 'allowed': False}]:
            with self.subTest(state=state):
                self.assertFalse(self.refresh(generate=forbidden, usage=lambda: state))

    def test_prompt_is_private_bounded_and_keeps_late_requirements(self):
        service = self.service()
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.preferences = {**profile.preferences,
                'application_details': {'email': 'secret@example.org', 'birth_date': '1970-01-01',
                    'additional_information': 'Unrelated confidential note',
                    'experiences': [{'title': 'Engineer', 'description': 'Built services'}],
                    'preferences': {'sponsorship_required': True}},
                'application_personalization': {'career_goals': 'Grow as an engineer', 'writing_samples': 'PRIVATE SAMPLE'}}
            job = session.get(models.Job, self.job_id)
            job.description = 'About us:\n' + 'A large company. ' * 10000 + '\nRequirements:\nGerman C1 required.'
            prompt = service.build_prompt(job, profile)
            self.assertLess(len(prompt), 55000)
            for secret in ['secret@example.org', '1970-01-01', 'Unrelated confidential note', 'PRIVATE SAMPLE']:
                self.assertNotIn(secret, prompt)
            for fact in ['Grow as an engineer', 'German C1 required', 'Built services', 'sponsorship_required']:
                self.assertIn(fact, prompt)
            self.assertIn('truncated', prompt.lower())

    def test_malformed_candidate_backoff_does_not_starve_next_candidate(self):
        with self.factory() as session:
            original = session.get(models.Job, self.job_id)
            job = models.Job(feed_id=original.feed_id, title='Software developer', description=DESCRIPTION,
                external_id='two', url='https://example.org/jobs/two', is_remote=True)
            session.add(job); session.commit(); second_id = job.id
            ensure_profile_match_indexes(session, [session.get(models.UserProfile, self.profile_id)])
            # Source addition requires incremental refresh when profile is already indexed.
            from hiring_scraper.app.job_search import refresh_application_matches_for_jobs
            refresh_application_matches_for_jobs(session, [second_id]); session.commit()
        def malformed(prompt, schema, **kwargs):
            return {'score': 100}
        with self.assertLogs("hiring_scraper.app.profile_llm", level="WARNING"):
            self.assertFalse(self.refresh(generate=malformed))
        self.assertTrue(self.refresh())
        with self.factory() as session:
            self.assertIsNotNone(session.get(models.ProfileLLMScore, (self.profile_id, second_id)))

    def test_ineligible_closed_and_implausible_candidates_are_not_scored(self):
        with self.factory() as session:
            original = session.get(models.Job, self.job_id)
            original.is_active = False
            session.add(models.Job(feed_id=original.feed_id, title='Nurse', description='Provide bedside care.',
                external_id='nurse', url='https://example.org/nurse', is_remote=True))
            session.commit()
        def forbidden(*args, **kwargs):
            self.fail('No plausible active candidate')
        self.assertFalse(self.refresh(generate=forbidden))

    def test_assessment_reads_are_batched_on_unindexed_cards(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            jobs = session.scalars(select(models.Job)).all()
            profile = session.get(models.UserProfile, self.profile_id)
            statements = []
            def observe(conn, cursor, statement, parameters, context, many):
                if 'FROM profile_llm_scores' in statement:
                    statements.append(statement)
            event.listen(self.engine, 'before_cursor_execute', observe)
            try:
                page = api._jobs_page(jobs, 'match', 0, 40, 49., 8.4, 35, profile, 0, True,
                    precomputed_scopes={job.id: {'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'remote'} for job in jobs})
            finally:
                event.remove(self.engine, 'before_cursor_execute', observe)
            self.assertEqual(page['items'][0]['profile_match']['score'], 90)
            self.assertEqual(len(statements), 1)

    def test_goal_edit_locks_profile_before_deleting_cached_assessment(self):
        self.assertTrue(self.refresh())
        operations = []
        def observe(conn, cursor, statement, parameters, context, many):
            compiled = getattr(context, 'compiled', None)
            query = getattr(compiled, 'statement', None)
            if getattr(query, '_for_update_arg', None) is not None and 'user_profiles' in statement:
                operations.append('profile_lock')
            if statement.startswith('DELETE FROM profile_llm_scores'):
                operations.append('cache_delete')
        event.listen(self.engine, 'before_cursor_execute', observe)
        try:
            with self.factory() as session:
                profiles.update_profile(self.profile_id, profiles.ProfileRequest(**PROFILE,
                    application_personalization={'career_goals': 'Lead a research team.'}), session)
        finally:
            event.remove(self.engine, 'before_cursor_execute', observe)
        self.assertIn('profile_lock', operations)
        self.assertIn('cache_delete', operations)
        self.assertLess(operations.index('profile_lock'), operations.index('cache_delete'))

    def test_external_scope_uncertainty_is_preserved_after_llm_authority_on_every_read(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            indexed = session.get(models.ProfileJobMatch, (self.profile_id, self.job_id))
            scope = {'eligible': True, 'reason': None,
                     'unknowns': ['Work location is not verified'], 'match_kind': 'remote'}
            with patch.object(api, '_result_scope', return_value=scope):
                detail = api.get_job(self.job_id, session=session, profile_id=self.profile_id,
                                     latitude=49., longitude=8.4, radius_km=35)['profile_match']
            self.assertEqual(detail['fit_tier'], 'possible')
            self.assertTrue(detail['uncertain'])
            for indexed_matches in [None, {job.id: indexed}]:
                with self.subTest(indexed=indexed_matches is not None):
                    page = api._jobs_page([job], 'match', 0, 40, 49., 8.4, 35, profile, 0, True,
                        indexed_matches=indexed_matches, precomputed_scopes={job.id: scope})
                    match = page['items'][0]['profile_match']
                    self.assertEqual(match['score'], 90)
                    self.assertEqual(match['fit_tier'], detail['fit_tier'])
                    self.assertEqual(match['uncertain'], detail['uncertain'])
                    self.assertIn('Work location is not verified', match['unknowns'])
                    hidden = api._jobs_page([job], 'match', 0, 40, 49., 8.4, 35, profile, 0, False,
                        indexed_matches=indexed_matches, precomputed_scopes={job.id: scope})
                    self.assertEqual(hidden['total'], 0)

    def test_matching_status_is_reachable_through_actual_asgi_routing(self):
        service = self.service()
        quota = {'available': True, 'allowed': False, 'reason': 'usage_limit_reached',
                 'used_percent': 50, 'resets_at': 1800000000, 'window_minutes': 300,
                 'limit_percent': 50, 'private_token': 'must-not-be-exposed'}
        async def request():
            messages = []
            async def receive():
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            async def send(message):
                messages.append(message)
            await api.app({'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
                'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
                'path': '/api/v1/profile-matching/status', 'raw_path': b'/api/v1/profile-matching/status',
                'query_string': b'', 'root_path': '', 'headers': [],
                'server': ('test', 80), 'client': ('test', 1234)}, receive, send)
            status = next(item['status'] for item in messages if item['type'] == 'http.response.start')
            body = b''.join(item.get('body', b'') for item in messages if item['type'] == 'http.response.body')
            return status, json.loads(body)
        with patch.object(service, 'SessionLocal', self.factory), \
             patch.object(service.codex_runtime, 'subscription_usage', return_value=quota):
            status, body = asyncio.run(request())
        self.assertEqual(status, 200)
        self.assertEqual(body['state'], 'paused')
        self.assertEqual(body['usage']['reason'], 'usage_limit_reached')
        self.assertEqual(body['usage']['used_percent'], 50)
        self.assertNotIn('private_token', body['usage'])

    def test_indexed_cards_include_assessment_provenance(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            indexed = session.get(models.ProfileJobMatch, (self.profile_id, self.job_id))
            page = api._jobs_page([job], 'match', 0, 40, 49., 8.4, 35, profile, 0, True,
                indexed_matches={job.id: indexed}, precomputed_scopes={job.id: {
                    'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'remote'}})
            self.assertEqual(page['items'][0]['profile_match']['score_source'], 'llm')
            self.assertEqual(page['items'][0]['profile_match']['llm_match']['dimensions']['skills']['score'], 90)

    def test_independent_memory_databases_do_not_contend_for_inference_lease(self):
        service = self.service()
        for url in ['sqlite://', 'sqlite:///:memory:']:
            first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
            try:
                with self.subTest(url=url), service._inference_lease(first) as acquired:
                    self.assertTrue(acquired)
                    with service._inference_lease(first) as same_database:
                        self.assertFalse(same_database)
                    with service._inference_lease(second) as distinct_database:
                        self.assertTrue(distinct_database)
            finally:
                first.dispose(); second.dispose()

    def test_named_shared_memory_database_engines_share_data_and_inference_lease(self):
        service = self.service()
        url = f'sqlite:///file:llm-{uuid4().hex}?mode=memory&cache=shared&uri=true'
        first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
        try:
            with first.begin() as connection:
                connection.exec_driver_sql('CREATE TABLE shared_facts (value INTEGER)')
                connection.exec_driver_sql('INSERT INTO shared_facts VALUES (42)')
            with second.connect() as connection:
                self.assertEqual(connection.exec_driver_sql('SELECT value FROM shared_facts').scalar_one(), 42)
            with service._inference_lease(first) as acquired:
                self.assertTrue(acquired)
                with service._inference_lease(second) as same_database:
                    self.assertFalse(same_database)
        finally:
            first.dispose(); second.dispose()

    def test_named_private_memory_database_engines_have_independent_leases(self):
        service = self.service()
        url = f'sqlite:///file:llm-{uuid4().hex}?mode=memory&cache=private&uri=true'
        first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
        try:
            with service._inference_lease(first) as acquired:
                self.assertTrue(acquired)
                with service._inference_lease(second) as distinct_database:
                    self.assertTrue(distinct_database)
        finally:
            first.dispose(); second.dispose()

    def test_memory_query_without_uri_flag_keeps_file_database_lease_shared(self):
        service = self.service()
        with tempfile.TemporaryDirectory() as folder:
            url = 'sqlite:///' + str(Path(folder) / 'file:ordinary.db') + '?mode=memory&cache=shared'
            # This intentionally ignored query models a real filename, not a URI.
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='Query string argument.*not accepted.*', category=SAWarning)
                first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
            try:
                with service._inference_lease(first) as acquired:
                    self.assertTrue(acquired)
                    with service._inference_lease(second) as same_database:
                        self.assertFalse(same_database)
            finally:
                first.dispose(); second.dispose()

    def test_same_file_database_engines_share_inference_lease(self):
        service = self.service()
        with tempfile.TemporaryDirectory() as folder:
            url = 'sqlite:///' + str(Path(folder) / 'shared.db')
            first, second = create_engine(url, poolclass=StaticPool), create_engine(url, poolclass=StaticPool)
            try:
                with service._inference_lease(first) as acquired:
                    self.assertTrue(acquired)
                    with service._inference_lease(second) as same_database:
                        self.assertFalse(same_database)
            finally:
                first.dispose(); second.dispose()

    def test_inference_lease_prevents_concurrent_duplicate_work(self):
        def generate(prompt, schema, **kwargs):
            self.assertFalse(self.refresh(generate=lambda *a, **kw: self.fail('Duplicate inference')))
            self.assertEqual(kwargs, {'model': 'gpt-6-luna', 'reasoning_effort': 'medium', 'usage_limit_percent': 50})
            return assessment()
        self.assertTrue(self.refresh(generate=generate))

    def test_full_rebuild_rejects_concurrently_changed_assessment(self):
        self.assertTrue(self.refresh())
        service = self.service()
        from hiring_scraper.app import job_search
        changed = False
        original = job_search.match_job
        def match(*args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                with self.factory() as writer:
                    row = writer.get(models.ProfileLLMScore, (self.profile_id, self.job_id))
                    row.assessment = assessment(1)
                    writer.commit()
            return original(*args, **kwargs)
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            profile.match_index_version = None; session.commit()
            with patch.object(job_search, 'match_job', match), self.assertRaises(job_search.ProfileIndexChanged):
                ensure_profile_match_indexes(session, [profile])

    def test_cv_career_facts_are_used_without_contacts_and_fact_edits_invalidate(self):
        service = self.service()
        from tests.test_tex_cv import source
        cv_text = source().replace('Built research tools.', 'Built Python research systems.')
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            session.add(models.ProfileCV(profile=profile, filename='cv.tex', content_type='text/plain',
                size_bytes=len(cv_text), data=cv_text.encode(), extracted_text=cv_text,
                variants={'en': {'text': cv_text, 'filename': 'cv.tex'}}))
            session.commit()
        def generate(prompt, schema, **kwargs):
            self.assertIn('Built Python research systems.', prompt)
            self.assertNotIn('Alex Example', prompt)
            return assessment()
        self.assertTrue(self.refresh(generate=generate))
        with self.factory() as session:
            cv = session.get(models.ProfileCV, self.profile_id)
            cv.extracted_text = cv_text.replace('Python research', 'Clinical research')
            cv.variants = {'en': {'text': cv.extracted_text, 'filename': 'cv.tex'}}
            session.commit()
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
            self.assertTrue(profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))

    def test_prompt_bounds_wide_metadata_and_profile_lists(self):
        service = self.service()
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            job.raw_metadata = {f'key-{index}': 'content ' * 100 for index in range(1500)}
            profile = session.get(models.UserProfile, self.profile_id)
            prompt = service.build_prompt(job, profile)
            self.assertLess(len(prompt), 55000)

    def test_deleting_sources_cascades_assessment_on_sqlite(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            session.delete(session.get(models.Job, self.job_id)); session.commit()
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_existing_local_exclusions_veto_model_eligibility(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            # Cached result used only with matching inputs; apply an independent
            # objective local veto to prove numerical authority cannot bypass it.
            job = session.get(models.Job, self.job_id)
            service = self.service()
            documents = service.load_matching_documents(session, [job], [profile])
            result = service.apply_cached_assessment({'eligible': False, 'score': 0,
                'conflicts': ['Excluded user term'], 'unknowns': []}, job, profile, documents)
            self.assertEqual(result['score'], 90)
            self.assertFalse(result['eligible']); self.assertEqual(result['fit_tier'], 'unlikely')


class ManagedWorkerTests(unittest.TestCase):
    def test_worker_keeps_rule_and_semantic_cycles_when_llm_pauses(self):
        from hiring_scraper.app import profile_match_worker as worker
        self.assertTrue(hasattr(worker, 'refresh_llm_scores_once'), 'Managed worker does not schedule LLM scoring')
        events = []
        def stop(_seconds):
            raise InterruptedError('Stop managed loop')
        with patch.object(worker, 'refresh_profile_match_indexes_once', lambda: events.append('rules')), \
             patch.object(worker, 'refresh_semantic_documents_once', lambda: events.append('semantics')), \
             patch.object(worker, 'refresh_llm_scores_once', lambda: events.append('llm') or False), \
             patch.object(worker.time, 'sleep', stop):
            with self.assertRaises(InterruptedError):
                worker.main()
        self.assertEqual(events, ['rules', 'semantics', 'llm'])

class FurtherSourceTests(unittest.TestCase):
    setUp = PersistedAssessmentTests.setUp
    tearDown = PersistedAssessmentTests.tearDown
    service = PersistedAssessmentTests.service
    refresh = PersistedAssessmentTests.refresh

    def test_cv_contact_layout_edits_retain_assessment(self):
        from tests.test_tex_cv import source
        cv_text = source()
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            session.add(models.ProfileCV(profile=profile, filename='cv.tex', content_type='text/plain',
                size_bytes=len(cv_text), data=cv_text.encode(), extracted_text=cv_text,
                variants={'en': {'text': cv_text, 'filename': 'cv.tex'}}))
            session.commit()
        self.assertTrue(self.refresh())
        with self.factory() as session:
            cv = session.get(models.ProfileCV, self.profile_id)
            text = cv_text.replace('Alex Example', 'New Contact Name')
            cv.extracted_text = text
            cv.variants = {'en': {'text': text, 'filename': 'cv.tex'}}
            session.commit()
            self.assertIsNotNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_new_location_invalidates_cached_assessment_and_index(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            session.add(models.JobLocation(job_id=self.job_id, label='Berlin', country_code='DE'))
            session.commit()
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
            self.assertTrue(profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))

    def test_source_removal_during_inference_does_not_publish(self):
        def generate(prompt, schema, **kwargs):
            with self.factory() as session:
                session.delete(session.get(models.Job, self.job_id)); session.commit()
            return assessment()
        self.assertFalse(self.refresh(generate=generate))
        with self.factory() as session:
            self.assertIsNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))

    def test_incremental_refresh_rejects_concurrent_assessment_change(self):
        self.assertTrue(self.refresh())
        from hiring_scraper.app.job_search import ProfileIndexChanged, refresh_application_matches_for_jobs
        def change_assessment(_session):
            with self.factory() as writer:
                row = writer.get(models.ProfileLLMScore, (self.profile_id, self.job_id))
                row.assessment = assessment(2); writer.commit()
        with self.factory() as session:
            with self.assertRaises(ProfileIndexChanged):
                refresh_application_matches_for_jobs(session, [self.job_id], before_publish=change_assessment)

    def test_enrichment_only_updates_do_not_repeat_inference(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            session.get(models.Job, self.job_id).enrichment = {'version': 'new extractor'}
            session.commit()
            self.assertIsNotNone(session.get(models.ProfileLLMScore, (self.profile_id, self.job_id)))
        self.assertFalse(self.refresh(generate=lambda *args, **kwargs: self.fail('Unchanged submitted data')))

    def test_hash_mismatch_in_current_version_is_rescored_after_bulk_source_edit(self):
        self.assertTrue(self.refresh(assessment(4)))
        # Bulk ingestion bypasses ORM invalidation: the audit queue must still
        # inspect cached low scores instead of excluding them permanently.
        with self.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE jobs SET description = description || ' New Python duties.'")
        self.assertTrue(self.refresh(assessment(94)))
        with self.factory() as session:
            result = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
            self.assertEqual(result['score'], 94)

    def test_indexed_cards_recompute_local_baseline_when_assessment_hash_is_stale(self):
        self.assertTrue(self.refresh(assessment(4)))
        with self.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE jobs SET description = description || ' More Python services.'")
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            indexed = session.get(models.ProfileJobMatch, (self.profile_id, self.job_id))
            page = api._jobs_page([job], 'match', 0, 40, 49., 8.4, 35, profile, 0, True,
                indexed_matches={job.id: indexed}, precomputed_scopes={job.id: {
                    'eligible': True, 'reason': None, 'unknowns': [], 'match_kind': 'remote'}})
            self.assertGreaterEqual(page['items'][0]['profile_match']['score'], 65)
            self.assertEqual(page['items'][0]['profile_match']['score_source'], 'local')

    def test_late_requirement_before_long_benefits_is_retained_in_prompt(self):
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            job.description = ('About us:\n' + 'A large organization. ' * 1800 +
                '\nRequirements:\nGerman C1 required for customer presentations.\nBenefits:\n' +
                'We offer a friendly workplace and lunch. ' * 1800)
            prompt = self.service().build_prompt(job, session.get(models.UserProfile, self.profile_id))
            self.assertIn('German C1 required', prompt)
            self.assertLess(len(prompt), 55000)

    def test_paused_quota_keeps_cached_assessments_readable(self):
        self.assertTrue(self.refresh())
        self.assertFalse(self.refresh(generate=lambda *a, **kw: self.fail('Quota blocked'),
            usage=lambda: {'allowed': False, 'reason': 'usage_limit_reached', 'used_percent': 50}))
        with self.factory() as session:
            self.assertEqual(api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']['score'], 90)

    def test_cached_assessment_hash_mismatch_falls_back_without_inference(self):
        self.assertTrue(self.refresh())
        with self.factory() as session:
            row = session.get(models.ProfileLLMScore, (self.profile_id, self.job_id))
            row.source_hash = '0' * 64; session.commit()
            result = api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']
            self.assertEqual(result['score_source'], 'local')
            self.assertIsNone(result['llm_match'])
