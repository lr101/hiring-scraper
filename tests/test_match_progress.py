"""Durable, bounded Journey indexing and transactional source invalidation."""
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import job_search, models, source_changes
from tests import pg


class MatchProgressTests(unittest.TestCase):
    def setUp(self):
        self.engine = pg.engine(self)
        models.Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, autoflush=False, expire_on_commit=False)
        with self.factory.begin() as session:
            profile = models.UserProfile(name='Developer', preferences={'desired_roles': ['Developer']})
            feed = models.JobFeed(company=models.Company(source='test', source_id='one', name='Employer'),
                                  provider='rss', status='parsed', feed_url='https://example.test/jobs')
            jobs = [models.Job(feed=feed, external_id=str(i), title='Developer',
                               url=f'https://example.test/jobs/{i}', is_remote=True) for i in range(5)]
            session.add_all([profile, *jobs]); session.flush()
            self.profile_id = profile.id
            self.job_ids = [job.id for job in jobs]
            # Ingestion declares new postings, which queues them for matching.
            source_changes.jobs_changed(session, self.job_ids)

    def tearDown(self):
        self.engine.dispose()

    def step(self):
        with patch.object(job_search, 'SessionLocal', self.factory), patch.object(job_search, '_MATCH_WORK_PAGE_SIZE', 2):
            return job_search.refresh_profile_match_indexes_once()

    def test_backfill_commits_bounded_progress_and_survives_restart(self):
        self.assertTrue(self.step())
        with self.factory() as session:
            matches = session.scalars(select(models.ProfileJobMatch)).all()
            self.assertEqual(len(matches), 2)
            self.assertTrue(job_search.profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))
        self.assertTrue(self.step())
        self.assertTrue(self.step())
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(models.ProfileJobMatch)).all()), 5)
            self.assertFalse(job_search.profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))

    def test_source_changes_are_coalesced_transactional_and_drained(self):
        with self.factory() as session:
            session.get(models.Job, self.job_ids[0]).description = 'first edit'
            source_changes.jobs_changed(session, [self.job_ids[0]])
            session.commit()
            first = session.get(models.MatchDirtyJob, self.job_ids[0]).revision
            session.get(models.Job, self.job_ids[0]).description = 'rolled back'
            source_changes.jobs_changed(session, [self.job_ids[0]])
            session.flush(); session.rollback()
            self.assertEqual(session.get(models.MatchDirtyJob, self.job_ids[0]).revision, first)
            session.get(models.Job, self.job_ids[0]).description = 'latest edit'
            source_changes.jobs_changed(session, [self.job_ids[0]])
            session.commit()
            self.assertNotEqual(session.get(models.MatchDirtyJob, self.job_ids[0]).revision, first)
        with patch.object(job_search, 'SessionLocal', self.factory):
            self.assertTrue(job_search.refresh_dirty_job_matches_once())
        with self.factory() as session:
            self.assertEqual(session.scalars(select(models.MatchDirtyJob)).all(), [])
            self.assertEqual(len(session.scalars(select(models.ProfileJobMatch)).all()), 5)

    def test_backfill_rotates_profiles_before_returning_to_large_inventory(self):
        with self.factory.begin() as session:
            second = models.UserProfile(name='Second', preferences={'desired_roles': ['Developer']})
            session.add(second); session.flush(); second_id = second.id
        self.step(); self.step()
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(models.ProfileJobMatch).where(
                models.ProfileJobMatch.profile_id == second_id)).all()), 2)

    def test_worker_advances_local_pages_before_secondary_stages(self):
        from hiring_scraper.app import profile_match_worker
        class EndIteration(BaseException):
            pass
        observed_cursors = []
        def expensive_work():
            with self.factory() as session:
                progress = session.get(models.ProfileMatchProgress, self.profile_id)
                self.assertIsNotNone(progress, 'Local backfill must commit before later stages')
                observed_cursors.append(progress.cursor)
        with patch.object(job_search, 'SessionLocal', self.factory), \
                patch.object(job_search, '_MATCH_WORK_PAGE_SIZE', 2), \
                patch.object(profile_match_worker, 'refresh_semantic_documents_once', expensive_work), \
                patch.object(profile_match_worker, 'prepare_once', expensive_work), \
                patch.object(profile_match_worker.time, 'sleep', side_effect=EndIteration):
            with self.assertLogs(profile_match_worker.LOG, level='INFO') as logs:
                with self.assertRaises(EndIteration):
                    profile_match_worker.main()
            self.assertFalse(any('ERROR' in line for line in logs.output), logs.output)
        self.assertEqual(observed_cursors, [self.job_ids[1]] * 2)
        with self.factory() as session:
            self.assertIsNotNone(session.get(models.ProfileMatchProgress, self.profile_id))

    def test_unrelated_ingestion_does_not_discard_a_verified_page(self):
        original = job_search.match_job
        changed = False
        def match_with_ingestion(*args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                with self.factory.begin() as writer:
                    writer.get(models.Job, self.job_ids[-1]).description = 'New unrelated source'
            return original(*args, **kwargs)
        with patch.object(job_search, 'match_job', side_effect=match_with_ingestion):
            self.assertTrue(self.step())
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(models.ProfileJobMatch)).all()), 2)

    def test_changed_batch_source_cannot_publish_stale_scores(self):
        with self.factory() as session:
            def change_source(publish_session):
                publish_session.get(models.Job, self.job_ids[0]).description = 'Changed source'
                publish_session.flush()
            with self.assertRaises(job_search.ProfileIndexChanged):
                job_search._refresh_application_matches_for_jobs(session, [self.job_ids[0]],
                    verify_sources=True, before_publish=change_source)
            session.rollback()
            self.assertEqual(session.scalars(select(models.ProfileJobMatch)).all(), [])
            self.assertIsNotNone(session.get(models.MatchDirtyJob, self.job_ids[0]))

    def test_profile_edit_restarts_checkpoint_and_reconciles_prior_pages(self):
        self.step()
        with self.factory.begin() as session:
            session.get(models.UserProfile, self.profile_id).preferences = {'excluded_terms': ['Developer']}
        self.step(); self.step(); self.step()
        with self.factory() as session:
            self.assertEqual(session.scalars(select(models.ProfileJobMatch)).all(), [])
            self.assertFalse(job_search.profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))

    def test_preference_round_trip_restarts_cleared_pages(self):
        self.step()
        with self.factory.begin() as session:
            session.query(models.MatchDirtyJob).delete()
        for preferences in ({'excluded_terms': ['Developer']}, {'desired_roles': ['Developer']}):
            with self.factory.begin() as session:
                profile = session.get(models.UserProfile, self.profile_id)
                profile.preferences = preferences
                profile.match_index_version = None
                session.flush()
                job_search.clear_profile_match_scores(session, self.profile_id)
        self.step(); self.step(); self.step()
        with self.factory() as session:
            self.assertEqual(set(session.scalars(select(models.ProfileJobMatch.job_id))), set(self.job_ids))
            self.assertFalse(job_search.profile_match_index_stale(session.get(models.UserProfile, self.profile_id)))

    def test_conflicted_backfill_does_not_block_dirty_queue_progress(self):
        from hiring_scraper.app import profile_match_worker
        class EndIteration(BaseException):
            pass
        with patch.object(job_search, 'SessionLocal', self.factory), \
                patch.object(profile_match_worker, 'refresh_profile_match_indexes_once',
                             side_effect=job_search.ProfileIndexChanged('retry page')), \
                patch.object(profile_match_worker.time, 'sleep', side_effect=EndIteration), \
                patch.object(profile_match_worker, 'refresh_semantic_documents_once', lambda: None), \
                patch.object(profile_match_worker, 'prepare_once', lambda: None):
            with self.assertLogs(profile_match_worker.LOG):
                with self.assertRaises(EndIteration):
                    profile_match_worker.main()
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(models.ProfileJobMatch)).all()), 5)

    def test_steady_dirty_queue_does_not_starve_later_stages_after_backfill_finishes(self):
        from hiring_scraper.app import profile_match_worker
        with patch.object(job_search, 'SessionLocal', self.factory):
            self.assertTrue(job_search.refresh_profile_match_indexes_once())
        class EndIteration(BaseException):
            pass
        events = []
        with patch.object(job_search, 'SessionLocal', self.factory), \
                patch.object(job_search, '_MATCH_WORK_PAGE_SIZE', 2), \
                patch.object(profile_match_worker.time, 'sleep', side_effect=EndIteration), \
                patch.object(profile_match_worker, 'refresh_semantic_documents_once', lambda: events.append('semantic')), \
                patch.object(profile_match_worker, 'prepare_once', lambda: events.append('prepare')):
            with self.assertRaises(EndIteration):
                profile_match_worker.main()
        self.assertEqual(events, ['semantic', 'prepare'])
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(models.MatchDirtyJob)).all()), 3)

    def test_each_source_and_evidence_event_keeps_a_durable_refresh(self):
        from sqlalchemy import delete
        cases = [
            ('location', [self.job_ids[0]]),
            ('feed', self.job_ids),
            ('company', self.job_ids),
            ('semantic', [self.job_ids[0]]),
            ('closure', [self.job_ids[0]]),
        ]
        for kind, expected in cases:
            with self.subTest(kind=kind):
                with self.factory.begin() as session:
                    session.execute(delete(models.MatchDirtyJob))
                    job = session.get(models.Job, self.job_ids[0])
                    # Each write path pairs its write with the matching declaration.
                    if kind == 'location':
                        job.locations.append(models.JobLocation(label='Berlin', country_code='DE'))
                        source_changes.jobs_changed(session, [job.id])
                    elif kind == 'feed':
                        job.feed.provider = 'greenhouse'
                        source_changes.feeds_changed(session, [job.feed_id])
                    elif kind == 'company':
                        job.feed.company.name = 'Renamed employer'
                        source_changes.companies_changed(session, [job.feed.company_id])
                    elif kind == 'semantic':
                        session.add(models.MatchSemanticDocument(job_id=job.id, source_hash='source',
                            version='test', sections=[], vectors=b''))
                        source_changes.vectors_changed(session, job_ids=[job.id])
                    else:
                        job.is_active = False
                        source_changes.jobs_changed(session, [job.id])
                with self.factory() as session:
                    self.assertEqual(session.scalars(select(models.MatchDirtyJob.job_id)
                        .order_by(models.MatchDirtyJob.job_id)).all(), expected)
