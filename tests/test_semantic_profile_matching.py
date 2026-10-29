"""Local semantic evidence must recover paraphrases without inventing eligibility."""
import importlib
import importlib.util
import os
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import Session, sessionmaker

from hiring_scraper.app import api, models, profiles

from hiring_scraper.matching import match_job


PROFILE = {'desired_roles': ['Software developer'], 'summary':
           'I build dependable web services, design relational data storage, '
           'and automate delivery of software changes.'}
DESCRIPTION = ('Your responsibilities:\nImplement server-side application logic, maintain databases, '
               'and streamline deployment workflows. Work with colleagues to document and deliver '
               'reliable customer integrations. ' * 3)


def engine(test):
    test.assertIsNotNone(importlib.util.find_spec('hiring_scraper.semantic_matching'),
                         'Section-based semantic matching is not implemented')
    return importlib.import_module('hiring_scraper.semantic_matching')


def vector(first, second=0):
    return [first, second] + [0.0] * 382


class SemanticSectionTests(unittest.TestCase):
    def test_duties_and_late_requirements_survive_long_intro_without_benefits_or_title(self):
        sem = engine(self)
        job = {'title': 'Python developer', 'description':
               'About us:\n' + 'We are an established employer with a modern workplace. ' * 100 +
               '\nYour tasks:\n' + 'Implement server-side logic and relational storage. ' * 50 +
               '\nRequirements:\nMaintain distributed delivery workflows.\nBenefits:\nFree Python training.'}
        parts = sem.job_sections(job)
        text = ' '.join(row['text'] for row in parts)
        self.assertIn('Maintain distributed delivery workflows', text)
        self.assertIn('Implement server-side logic', text)
        self.assertNotIn('Python', text)
        self.assertNotIn('modern workplace', text)
        self.assertLessEqual(len(parts), 12)
        self.assertTrue(all(len(row['text']) <= 900 for row in parts))

    def test_profile_uses_career_facts_without_contacts_desired_titles_or_aspirations(self):
        sem = engine(self)
        parts = sem.profile_sections({**PROFILE, 'desired_roles': ['Astronaut'],
            'application_details': {'email': 'private@example.org'},
            'application_personalization': {'career_goals': 'Learn quantum physics.'}})
        text = ' '.join(row['text'] for row in parts)
        self.assertIn('relational data storage', text)
        self.assertNotIn('private', text)
        self.assertNotIn('Astronaut', text)
        self.assertNotIn('quantum', text)
        self.assertEqual(sem.profile_sections({'desired_roles': ['Astronaut']}), [])

    def test_benefits_optional_training_and_title_only_listings_provide_no_vectors(self):
        sem = engine(self)
        for job in [{'title': 'Backend specialist'},
                    {'title': 'Specialist', 'description': 'Benefits:\nBuild web services in our training program.'},
                    {'title': 'Specialist', 'description': 'Preferred qualifications:\nWeb development.'}]:
            with self.subTest(job=job):
                self.assertEqual(sem.job_sections(job), [])

    def test_repeated_duties_do_not_duplicate_the_embedding_input(self):
        sem = engine(self)
        text = 'Implement server-side application logic, maintain databases, and streamline deployment workflows.'
        parts = sem.job_sections({'description': 'Your tasks:\n' + (text + ' ') * 5})
        self.assertEqual([part['text'] for part in parts], [text])

    def test_matching_vectors_recover_a_possible_lead_and_orthogonal_vectors_do_not(self):
        sem = engine(self)
        p = sem.profile_sections(PROFILE)
        j = sem.job_sections({'description': DESCRIPTION})
        p_blob = sem.pack_vectors([vector(1)] * len(p))
        similar = sem.semantic_evidence(p, p_blob, j, sem.pack_vectors([vector(1)] * len(j)))
        unrelated = sem.semantic_evidence(p, p_blob, j, sem.pack_vectors([vector(0, 1)] * len(j)))
        self.assertGreater(similar['score'], 0)
        self.assertLess(similar['score'], 65)
        self.assertEqual(unrelated['score'], 0)
        self.assertIn('server-side', similar['job_evidence'])

    def test_malformed_or_nonfinite_vectors_cannot_supply_similarity(self):
        sem = engine(self)
        for vectors in [[[0.0] * 383], [[float('nan')] * 384], [[0.0] * 384]]:
            with self.subTest(length=len(vectors[0])):
                with self.assertRaises(ValueError):
                    sem.pack_vectors(vectors)


class SemanticScoreTests(unittest.TestCase):
    def evidence(self):
        return {'score': 59, 'similarity': .95, 'model': 'local-e5',
                'job_evidence': 'Implement server-side application logic.',
                'profile_evidence': 'I build dependable web services.'}

    def test_semantic_evidence_recovers_an_unfamiliar_title_as_a_possible_lead(self):
        result = match_job({'title': 'Solutions Specialist', 'description': DESCRIPTION}, PROFILE,
                           semantic=self.evidence())
        self.assertGreaterEqual(result['score'], 30)
        self.assertEqual(result['fit_tier'], 'possible')
        self.assertTrue(result['uncertain'])
        self.assertTrue(any('semantic' in reason.lower() for reason in result['reasons']))

    def test_semantic_evidence_cannot_override_language_or_qualification_checks(self):
        for requirement in ['German C1 required.', 'PMP certification required.']:
            with self.subTest(requirement=requirement):
                result = match_job({'title': 'Specialist', 'description': DESCRIPTION +
                                    '\nRequirements:\n' + requirement},
                                   {**PROFILE, 'languages': ['English']}, semantic=self.evidence())
                self.assertNotEqual(result['fit_tier'], 'recommended')
                if requirement.startswith('German'):
                    self.assertFalse(result['eligible'])
                else:
                    self.assertTrue(result['requirement_gaps'])

    def test_lexical_strong_matches_are_not_downgraded_by_semantic_similarity(self):
        job = {'title': 'Developer', 'description': 'Your tasks: Build Python and SQL services. ' * 20}
        profile = {'skills': ['Python', 'SQL'], 'desired_roles': ['Software developer']}
        result = match_job(job, profile, semantic=self.evidence())
        self.assertEqual(result['fit_tier'], 'recommended')
        self.assertEqual(result['score'], match_job(job, profile)['score'])

    def test_language_bonuses_cannot_promote_semantic_only_evidence_to_recommended(self):
        job = {'title': 'Specialist', 'description': DESCRIPTION +
               '\nRequirements:\nGerman C1 and English C1 required.'}
        profile = {**PROFILE, 'language_levels': {'German': 'C1', 'English': 'C1'}}
        result = match_job(job, profile, semantic=self.evidence())
        self.assertTrue(result['eligible'])
        self.assertEqual(result['fit_tier'], 'possible')
        self.assertLess(result['score'], 65)

    def test_independently_qualified_rule_match_keeps_its_score_and_confidence(self):
        job = {'title': 'Software developer', 'description': DESCRIPTION +
               '\nRequirements:\nGerman C1 and English C1 required.'}
        profile = {**PROFILE, 'language_levels': {'German': 'C1', 'English': 'C1'}}
        baseline = match_job(job, profile)
        result = match_job(job, profile, semantic=self.evidence())
        self.assertEqual(baseline['fit_tier'], 'recommended')
        self.assertEqual(result['score'], baseline['score'])
        self.assertEqual(result['uncertain'], baseline['uncertain'])


class SemanticCacheIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        with self.factory() as session:
            self.profile_id = profiles.create_profile(profiles.ProfileRequest(name='Developer', **PROFILE,
                matching_defaults={'min_match_score': 30}), session)['id']
            company = models.Company(source='test', source_id='semantic', name='Example')
            feed = models.JobFeed(company=company, provider='html_jobs', status='parsed',
                                  feed_url='https://example.org/jobs')
            job = models.Job(feed=feed, title='Solutions Specialist', description=DESCRIPTION,
                             external_id='one', url='https://example.org/jobs/one', is_remote=True)
            session.add(company); session.commit()
            self.job_id = job.id

    def tearDown(self):
        self.engine.dispose()

    def service(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.profile_semantics'),
                             'Background semantic matching is not implemented')
        return importlib.import_module('hiring_scraper.app.profile_semantics')

    def populate(self):
        service = self.service()
        def encode(texts, kind):
            self.assertIn(kind, {'query', 'passage'})
            self.assertTrue(all('private@example.org' not in text for text in texts))
            return [vector(1) for _ in texts]
        with patch.object(service, 'SessionLocal', self.factory):
            self.assertTrue(service.refresh_semantic_documents_once(encode=encode))
        return service

    def test_background_vectors_add_a_missed_job_to_journey_and_detail_consistently(self):
        self.populate()
        with self.factory() as session:
            page = api.list_applications(profile_ids=str(self.profile_id), statuses='new',
                                         sort='match', offset=0, limit=40, session=session)
            self.assertEqual([item['id'] for item in page['items']], [self.job_id])
            match = page['items'][0]['profile_match']
            self.assertEqual(match['fit_tier'], 'possible')
            self.assertTrue(match['semantic_match'])
            detail = api.get_job(self.job_id, session=session, profile_id=self.profile_id)
            self.assertEqual(detail['profile_match']['score'], match['score'])

    def test_unchanged_sources_reuse_vectors_and_contacts_do_not_invalidate_them(self):
        service = self.populate()
        def no_inference(*args):
            self.fail('Unchanged career content should reuse the saved vectors')
        with self.factory() as session:
            profiles.update_profile(self.profile_id, profiles.ProfileRequest(name='Developer', **PROFILE,
                matching_defaults={'min_match_score': 30}, application_details={'email': 'private@example.org'}), session)
        with patch.object(service, 'SessionLocal', self.factory):
            self.assertFalse(service.refresh_semantic_documents_once(encode=no_inference))

    def test_posting_change_removes_stale_similarity_and_preserves_user_stage(self):
        service = self.populate()
        with self.factory() as session:
            api.list_applications(profile_ids=str(self.profile_id), statuses='all', sort='match',
                                  offset=0, limit=40, session=session)
            api.set_job_application(self.profile_id, self.job_id, api.ApplicationRequest(status='open'), session)
            job = session.get(models.Job, self.job_id)
            job.description = 'Your tasks: Provide bedside patient care and administer medication. ' * 8
            session.commit()
            detail = api.get_job(self.job_id, session=session, profile_id=self.profile_id)
            self.assertIsNone(detail['profile_match']['semantic_match'])
            self.assertLess(detail['profile_match']['score'], 30)
            self.assertEqual(detail['application']['status'], 'open')
        def unrelated(texts, kind):
            return [vector(0, 1) if kind == 'passage' else vector(1) for _ in texts]
        with patch.object(service, 'SessionLocal', self.factory):
            self.assertTrue(service.refresh_semantic_documents_once(encode=unrelated))

    def test_unavailable_model_keeps_rules_usable_and_retries_missing_vectors(self):
        service = self.service()
        with patch.object(service, 'SessionLocal', self.factory):
            with self.assertRaises(OSError):
                service.refresh_semantic_documents_once(encode=lambda *args: (_ for _ in ()).throw(OSError('offline')))
        with self.factory() as session:
            self.assertLess(api.get_job(self.job_id, session=session, profile_id=self.profile_id)['profile_match']['score'], 30)
        self.populate()

    def test_source_change_during_inference_discards_the_old_job_vector(self):
        service = self.service()
        changed = False
        def encode(texts, kind):
            nonlocal changed
            if kind == 'passage' and not changed:
                with self.factory() as session:
                    session.get(models.Job, self.job_id).description = 'Your tasks: Provide bedside patient care. ' * 12
                    session.commit()
                changed = True
            return [vector(1) for _ in texts]
        with patch.object(service, 'SessionLocal', self.factory):
            service.refresh_semantic_documents_once(encode=encode)
        with self.factory() as session:
            detail = api.get_job(self.job_id, session=session, profile_id=self.profile_id)
            self.assertIsNone(detail['profile_match']['semantic_match'])
            self.assertLess(detail['profile_match']['score'], 30)

    def test_stale_same_version_profile_vector_is_removed_and_rebuilt(self):
        service = self.populate()
        changed = False

        def change_after_snapshot(texts, kind):
            nonlocal changed
            if kind == 'query' and not changed:
                changed = True
                with self.factory.begin() as session:
                    profile = session.get(models.UserProfile, self.profile_id)
                    session.execute(update(models.UserProfile).where(
                        models.UserProfile.id == self.profile_id).values(preferences={
                            **profile.preferences,
                            'summary': 'I design electronic circuits and validate hardware.'
                        }))
                    # Reproduce a current-version row whose source changed after
                    # the worker selected its old source snapshot.
                    session.execute(update(models.MatchSemanticDocument).where(
                        models.MatchSemanticDocument.profile_id == self.profile_id).values(
                            version=service.VERSION))
            return [vector(1) for _ in texts]

        with self.factory.begin() as session:
            session.execute(update(models.MatchSemanticDocument).where(
                models.MatchSemanticDocument.profile_id == self.profile_id).values(version='older-version'))
        with patch.object(service, 'SessionLocal', self.factory):
            self.assertTrue(service.refresh_semantic_documents_once(encode=change_after_snapshot))
        with self.factory() as session:
            self.assertIsNone(session.scalar(select(models.MatchSemanticDocument).where(
                models.MatchSemanticDocument.profile_id == self.profile_id)))

        def encode_updated_profile(texts, kind):
            self.assertEqual(kind, 'query')
            return [vector(0, 1) for _ in texts]

        with patch.object(service, 'SessionLocal', self.factory):
            self.assertTrue(service.refresh_semantic_documents_once(encode=encode_updated_profile))
        with self.factory() as session:
            profile = session.get(models.UserProfile, self.profile_id)
            document = session.scalar(select(models.MatchSemanticDocument).where(
                models.MatchSemanticDocument.profile_id == self.profile_id))
            self.assertEqual(document.version, service.VERSION)
            self.assertEqual(document.source_hash,
                             service.source_hash(service.profile_sections(profile.matching_preferences)))

    def test_sqlite_publication_reserves_writer_against_profile_edits(self):
        service = self.service()
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f'sqlite:///{directory}/semantic.sqlite3',
                                   connect_args={'timeout': 5})
            models.Base.metadata.create_all(engine)
            factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
            with factory.begin() as session:
                profile = models.UserProfile(name='Developer', preferences={
                    'desired_roles': ['Software developer'],
                    'summary': PROFILE['summary'],
                    'matching_defaults': {'min_match_score': 30},
                })
                session.add(profile)
                session.flush()
                profile_id = profile.id

            attempted = threading.Event()
            completed = threading.Event()
            errors = []

            def edit_profile():
                try:
                    with factory() as session:
                        attempted.set()
                        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
                        profiles.update_profile(profile_id, profiles.ProfileRequest(
                            name='Developer', desired_roles=['Software developer'],
                            matching_defaults={'min_match_score': 30},
                            summary='I design circuits and validate electronic hardware.'), session)
                except Exception as error:
                    errors.append(error)
                finally:
                    completed.set()

            editor = None
            pack_vectors = service.pack_vectors

            def start_edit_during_publication(vectors):
                nonlocal editor
                editor = threading.Thread(target=edit_profile, daemon=True)
                editor.start()
                self.assertTrue(attempted.wait(1), 'Profile writer did not start')
                self.assertFalse(completed.wait(.2),
                                 'SQLite publication did not reserve its writer before verification')
                return pack_vectors(vectors)

            try:
                with patch.object(service, 'SessionLocal', factory), \
                     patch.object(service, 'pack_vectors', side_effect=start_edit_during_publication):
                    self.assertTrue(service.refresh_semantic_documents_once(
                        encode=lambda texts, kind: [vector(1) for _ in texts]))
                editor.join(timeout=5)
                self.assertFalse(editor.is_alive(), 'Profile edit did not resume after publication')
                self.assertEqual(errors, [])
                with factory() as session:
                    self.assertIsNone(session.scalar(select(models.MatchSemanticDocument).where(
                        models.MatchSemanticDocument.profile_id == profile_id)))

                with patch.object(service, 'SessionLocal', factory):
                    self.assertTrue(service.refresh_semantic_documents_once(
                        encode=lambda texts, kind: [vector(0, 1) for _ in texts]))
                with factory() as session:
                    profile = session.get(models.UserProfile, profile_id)
                    document = session.scalar(select(models.MatchSemanticDocument).where(
                        models.MatchSemanticDocument.profile_id == profile_id))
                    self.assertEqual(document.source_hash,
                                     service.source_hash(service.profile_sections(profile.matching_preferences)))
            finally:
                if editor is not None and editor.is_alive():
                    editor.join(timeout=5)
                engine.dispose()

    def test_sqlite_profile_edit_reserves_writer_before_semantic_cache_lookup(self):
        service = self.service()
        from sqlalchemy.engine.cursor import CursorResult
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f'sqlite:///{directory}/semantic.sqlite3',
                                   connect_args={'timeout': 5, 'check_same_thread': False})
            models.Base.metadata.create_all(engine)
            with engine.connect() as connection:
                connection.exec_driver_sql('PRAGMA journal_mode=WAL')
            factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
            with factory.begin() as session:
                profile = models.UserProfile(name='Developer', preferences={
                    'desired_roles': ['Software developer'],
                    'summary': PROFILE['summary'],
                    'matching_defaults': {'min_match_score': 30},
                })
                session.add(profile)
                session.flush()
                profile_id = profile.id

            editor_read_cache = threading.Event()
            resume_editor = threading.Event()
            publisher_attempted_lock = threading.Event()
            publisher_completed = threading.Event()
            errors = []
            editor = None
            publisher = None

            original_scalar = CursorResult.scalar

            def observe_publisher_lock(_connection, _cursor, statement, _parameters, _context, _many):
                if (threading.current_thread().name == 'semantic-publisher' and
                        statement.strip().upper() == 'BEGIN IMMEDIATE'):
                    publisher_attempted_lock.set()

            def edit_profile():
                try:
                    with factory() as session:
                        profiles.update_profile(profile_id, profiles.ProfileRequest(
                            name='Developer', desired_roles=['Software developer'],
                            matching_defaults={'min_match_score': 30},
                            summary='I design circuits and validate electronic hardware.'), session)
                except Exception as error:
                    errors.append(error)

            def publish_profile_vectors():
                try:
                    with patch.object(service, 'SessionLocal', factory):
                        service.refresh_semantic_documents_once(
                            encode=lambda texts, kind: [vector(1) for _ in texts])
                except Exception as error:
                    errors.append(error)
                finally:
                    publisher_completed.set()

            def scalar_with_interleaving(result):
                value = original_scalar(result)
                statement = result.context.statement.upper()
                if (threading.current_thread().name == 'profile-editor' and
                        'MATCH_SEMANTIC_DOCUMENTS.SOURCE_HASH' in statement):
                    editor_read_cache.set()
                    if not resume_editor.wait(5):
                        raise TimeoutError('Profile editor was not resumed')
                return value

            event.listen(engine, 'before_cursor_execute', observe_publisher_lock)
            try:
                editor = threading.Thread(target=edit_profile, name='profile-editor', daemon=True)
                with patch.object(CursorResult, 'scalar', new=scalar_with_interleaving):
                    editor.start()
                    self.assertTrue(editor_read_cache.wait(5), 'Editor did not read the semantic cache')
                    publisher = threading.Thread(target=publish_profile_vectors,
                                                 name='semantic-publisher', daemon=True)
                    publisher.start()
                    self.assertTrue(publisher_attempted_lock.wait(5), 'Publisher did not request its SQLite writer lock')
                    self.assertFalse(publisher_completed.wait(.2),
                                     'Publisher passed an editor that had already checked for a cache row')
                    resume_editor.set()
                    editor.join(timeout=5)
                    publisher.join(timeout=5)
                    self.assertFalse(editor.is_alive(), 'Profile edit did not resume')
                    self.assertFalse(publisher.is_alive(), 'Semantic publisher did not resume')
                    self.assertEqual(errors, [])

                    with factory() as session:
                        self.assertIsNone(session.scalar(select(models.MatchSemanticDocument).where(
                            models.MatchSemanticDocument.profile_id == profile_id)))
                    with patch.object(service, 'SessionLocal', factory):
                        self.assertTrue(service.refresh_semantic_documents_once(
                            encode=lambda texts, kind: [vector(0, 1) for _ in texts]))
                    with factory() as session:
                        self.assertIsNotNone(session.scalar(select(models.MatchSemanticDocument).where(
                            models.MatchSemanticDocument.profile_id == profile_id)))
            finally:
                resume_editor.set()
                if editor is not None and editor.is_alive():
                    editor.join(timeout=5)
                if publisher is not None and publisher.is_alive():
                    publisher.join(timeout=5)
                event.remove(engine, 'before_cursor_execute', observe_publisher_lock)
                engine.dispose()

    def test_profile_fact_changes_recompute_only_profile_vectors(self):
        service = self.populate()
        with self.factory() as session:
            profiles.update_profile(self.profile_id, profiles.ProfileRequest(name='Developer',
                desired_roles=['Software developer'], summary='I design integrated circuits and validate electronic hardware.'), session)
            self.assertIsNone(api.get_job(self.job_id, session=session,
                                         profile_id=self.profile_id)['profile_match']['semantic_match'])
        def encode(texts, kind):
            self.assertEqual(kind, 'query', 'Job vectors must be reused across profile changes')
            return [vector(0, 1) for _ in texts]
        with patch.object(service, 'SessionLocal', self.factory):
            self.assertTrue(service.refresh_semantic_documents_once(encode=encode))
        with self.factory() as session:
            self.assertLess(api.get_job(self.job_id, session=session,
                                       profile_id=self.profile_id)['profile_match']['score'], 30)

    def test_large_profile_sets_fit_the_standard_sqlite_parameter_limit(self):
        service = self.populate()
        with self.factory() as session:
            raw = session.connection().connection.driver_connection
            previous_limit = raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            try:
                documents = service.load_semantic_documents(session,
                    [session.get(models.Job, self.job_id)],
                    [session.get(models.UserProfile, self.profile_id)] +
                    [SimpleNamespace(id=index) for index in range(2, 1201)])
                self.assertIn(('job', self.job_id), documents)
                self.assertIn(('profile', self.profile_id), documents)
            finally:
                raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous_limit)

    def test_repeated_comparisons_parse_each_source_once_per_batch(self):
        service = self.populate()
        with self.factory() as session:
            job = session.get(models.Job, self.job_id)
            profile = session.get(models.UserProfile, self.profile_id)
            with patch.object(service, 'job_sections', wraps=service.job_sections) as job_extract, \
                    patch.object(service, 'profile_sections', wraps=service.profile_sections) as profile_extract:
                documents = service.load_semantic_documents(session, [job], [profile])
                for _ in range(20):
                    result = service.cached_semantic_evidence(
                        {'description': job.description, 'raw_metadata': job.raw_metadata},
                        profile.matching_preferences, job.id, profile.id, documents)
                    self.assertGreater(result['score'], 0)
                self.assertEqual(job_extract.call_count, 1, 'A cached job must not be reparsed for every profile')
                self.assertEqual(profile_extract.call_count, 1, 'A cached profile must not be reparsed for every job')

    def test_unindexed_jobs_page_loads_semantic_documents_once(self):
        self.populate()
        with self.factory() as session:
            original = session.get(models.Job, self.job_id)
            for index in range(3):
                session.add(models.Job(feed_id=original.feed_id, external_id=f'extra-{index}',
                    title=f'Specialist {index}', description=DESCRIPTION,
                    url=f'https://example.org/jobs/{index}', is_remote=True))
            session.commit()
            jobs = session.scalars(select(models.Job)).all()
            profile = session.get(models.UserProfile, self.profile_id)
            queries = []
            def observe(_conn, _cursor, statement, _parameters, _context, _many):
                if 'FROM match_semantic_documents' in statement:
                    queries.append(statement)
            event.listen(self.engine, 'before_cursor_execute', observe)
            try:
                page = api._jobs_page(jobs, 'match', 0, 40, 49.0, 8.4, 35, profile, 0, True,
                    precomputed_scopes={job.id: {'eligible': True, 'reason': None,
                                                 'unknowns': [], 'match_kind': 'remote'} for job in jobs})
            finally:
                event.remove(self.engine, 'before_cursor_execute', observe)
            self.assertEqual(page['total'], 4)
            self.assertEqual(len(queries), 1, 'Cached semantics must be fetched in a batch, not once per card')


@unittest.skipUnless(os.getenv('RUN_LOCAL_SEMANTIC_TESTS') == '1', 'requires the pinned local E5 model')
class RealLocalSemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hiring_scraper.app.semantic_search_worker import LocalE5
        cls.model = LocalE5()

    def test_english_and_german_paraphrases_surface_without_promoting_unrelated_work(self):
        sem = engine(self)
        p = sem.profile_sections(PROFILE)
        p_blob = sem.pack_vectors(self.model.encode(['query: ' + row['text'] for row in p]))
        for description, relevant in [
            ('Implement server-side application logic, maintain databases, and streamline deployment workflows.', True),
            ('Sie entwickeln serverseitige Anwendungen, betreuen relationale Datenbanken und automatisieren die Auslieferung neuer Softwareversionen.', True),
            ('Provide bedside patient care, administer medication and assist with clinical procedures.', False),
            ('Coordinate schedules, organize meetings and greet visitors at reception.', False),
        ]:
            with self.subTest(description=description):
                job = {'title': 'Specialist', 'description': 'Your tasks:\n' + description}
                parts = sem.job_sections(job)
                blob = sem.pack_vectors(self.model.encode(['passage: ' + row['text'] for row in parts]))
                evidence = sem.semantic_evidence(p, p_blob, parts, blob)
                result = match_job(job, PROFILE, semantic=evidence)
                self.assertEqual(result['score'] >= 30, relevant,
                                 f"Unexpected retrieval decision with cosine {evidence['similarity']}")
                self.assertNotEqual(result['fit_tier'], 'recommended')


if __name__ == '__main__':
    unittest.main()
