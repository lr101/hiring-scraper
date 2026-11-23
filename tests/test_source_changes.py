"""Write paths declare source changes; the detector reports any they forget."""
import unittest
from unittest.mock import patch

from sqlalchemy import select

from hiring_scraper.app import search_snapshots, source_changes
from hiring_scraper.app.models import (
    Company, Job, JobFeed, MatchDirtyJob, MatchSemanticDocument, UserProfile,
)
from hiring_scraper.semantic_matching import VERSION
from tests import pg


class SourceChangesTests(unittest.TestCase):
    def setUp(self):
        self.factory = pg.factory(self)
        with self.factory.begin() as session:
            feed = JobFeed(company=Company(source='test', source_id='c', name='Employer'),
                           provider='rss', feed_url='https://example.test/jobs', status='parsed')
            self.profile = UserProfile(name='Developer', preferences={'skills': ['Python']})
            job = Job(feed=feed, external_id='1', title='Developer', url='https://example.test/1',
                      description='You will build and operate reliable Python services.', is_remote=True)
            session.add_all([feed, self.profile, job])
            session.flush()
            self.job_id, self.profile_id, self.feed_id = job.id, self.profile.id, feed.id

    def application_write(self):
        """Pretend a flush comes from application code, which strict mode verifies."""
        return patch.object(source_changes, '_from_test_code', return_value=False)

    def test_undeclared_application_write_fails_in_strict_mode(self):
        with self.application_write(), self.factory() as session:
            session.get(Job, self.job_id).title = 'Renamed'
            with self.assertRaisesRegex(RuntimeError, 'undeclared source changes job:'):
                session.commit()

    def test_declared_write_commits_and_queues_rematch(self):
        with self.application_write(), self.factory() as session:
            session.get(Job, self.job_id).title = 'Renamed'
            source_changes.jobs_changed(session, [self.job_id])
            session.commit()
        with self.factory() as session:
            self.assertEqual(session.scalars(select(MatchDirtyJob.job_id)).all(), [self.job_id])

    def test_undeclared_write_is_logged_not_raised_in_production(self):
        with self.application_write(), patch.object(source_changes, 'strict', return_value=False), \
                self.assertLogs(source_changes.LOG, 'ERROR') as logs, self.factory() as session:
            session.get(JobFeed, self.feed_id).tenant = 'changed'
            session.commit()
        self.assertIn('feed:', logs.output[0])

    def test_declarations_publish_one_search_generation_at_commit(self):
        with self.factory() as session:
            before = search_snapshots.search_generation(session)
        with self.factory() as session:
            source_changes.feeds_changed(session, [self.feed_id])
            source_changes.companies_changed(session, [])
            session.commit()
        with self.factory() as session:
            self.assertGreater(search_snapshots.search_generation(session), before)

    def test_savepoints_neither_publish_early_nor_discard_outer_declarations(self):
        with self.factory() as session:
            before = search_snapshots.search_generation(session)
        published = []
        real_finish = source_changes.finish

        def finish(session):
            published.append(session.in_nested_transaction())
            real_finish(session)

        with self.application_write(), patch.object(source_changes, 'finish', finish), \
                self.factory() as session:
            session.get(Job, self.job_id).title = 'Renamed'
            source_changes.jobs_changed(session, [self.job_id])
            with session.begin_nested():  # released savepoint: no early publication
                session.execute(select(1))
            self.assertEqual(published, [])
            try:
                with session.begin_nested():  # e.g. a deferred hold_profiles(wait=False)
                    raise RuntimeError('savepoint rolled back')
            except RuntimeError:
                pass
            session.commit()
        self.assertEqual(published, [False])
        with self.factory() as session:
            self.assertGreater(search_snapshots.search_generation(session), before)
            self.assertEqual(session.scalars(select(MatchDirtyJob.job_id)).all(), [self.job_id])

    def test_deleting_a_profile_is_fully_declared(self):
        from hiring_scraper.app import profiles
        with self.factory() as session, patch('hiring_scraper.app.codex_runtime._root',
                                              return_value=__import__('pathlib').Path(self.tmp())):
            self.assertEqual(profiles.delete_profile(self.profile_id, session), {'deleted': True})
        with self.factory() as session:
            self.assertIsNone(session.get(UserProfile, self.profile_id))

    def tmp(self):
        import tempfile
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return directory.name

    def test_jobs_changed_drops_only_stale_vectors(self):
        from hiring_scraper.semantic_matching import job_sections, source_hash as vector_hash
        with self.factory.begin() as session:
            job = session.get(Job, self.job_id)
            session.add(MatchSemanticDocument(job_id=job.id, version=VERSION, sections=[], vectors=b'',
                source_hash=vector_hash(job_sections({'description': job.description,
                                                      'raw_metadata': job.raw_metadata}))))
        with self.factory() as session:
            # Unchanged inputs keep the cached rows.
            source_changes.jobs_changed(session, [self.job_id])
            session.commit()
            self.assertIsNotNone(session.scalar(select(MatchSemanticDocument.id)))
            session.get(Job, self.job_id).description = 'You will coordinate clinical trials across several hospital sites.'
            source_changes.jobs_changed(session, [self.job_id])
            session.commit()
            self.assertIsNone(session.scalar(select(MatchSemanticDocument.id)))

    def test_changed_career_facts_restart_the_profile_index(self):
        with self.factory.begin() as session:
            session.get(UserProfile, self.profile_id).match_index_version = 'current'
        with self.factory() as session:
            source_changes.profile_changed(session, self.profile_id, career_facts_changed=True)
            session.commit()
            self.assertIsNone(session.get(UserProfile, self.profile_id).match_index_version)

    def test_tracked_values_ignore_bookkeeping_fields(self):
        with self.factory() as session:
            company = session.scalar(select(Company))
            before = source_changes.tracked_values(company)
            company.crawl_priority = 99
            self.assertEqual(source_changes.tracked_values(company), before)
            company.name = 'Renamed'
            self.assertNotEqual(source_changes.tracked_values(company), before)


if __name__ == '__main__':
    unittest.main()
