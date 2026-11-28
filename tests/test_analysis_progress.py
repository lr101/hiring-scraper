"""Local matching progress: postings read, gated queue, skipped postings, and profile matches."""
import unittest
from datetime import timedelta

from hiring_scraper.app import journey, models
from hiring_scraper.app.analysis_gate import current_profile_set_hash, profile_embedding_hash, profile_embedding_text
from hiring_scraper.app.models import utcnow
from hiring_scraper.semantic_matching import MODEL_KEY
from tests import pg


class AnalysisProgressTests(unittest.TestCase):
    def setUp(self):
        self.session = pg.session(self)
        self.feed = models.JobFeed(
            company=models.Company(source='test', source_id='employer', name='Example Employer'),
            provider='rss', status='parsed', feed_url='https://example.test/jobs')
        self.profile = models.UserProfile(name='Analyst', preferences={'desired_roles': ['Data Analyst']})
        self.other = models.UserProfile(name='Other', preferences={'desired_roles': ['Designer']})
        self.session.add_all([self.feed, self.profile, self.other])
        self.session.flush()
        for profile in (self.profile, self.other):
            text = profile_embedding_text(profile.matching_preferences)
            self.session.add(models.SemanticSearchDocument(
                entity_type='profile', entity_id=profile.id,
                content_hash=profile_embedding_hash(text),
                model_revision=MODEL_KEY, embedding=[1.0] + [0.0] * 383,
            ))
        self.session.flush()
        self.profile_hash = current_profile_set_hash(self.session)

    def job(self, name, active=True, *, date_posted=None, first_seen_at=None):
        job = models.Job(feed=self.feed, external_id=name, title=name, url=f'https://example.test/{name}',
                         description='Text', is_active=active, date_posted=date_posted,
                         first_seen_at=first_seen_at or utcnow())
        self.session.add(job)
        self.session.flush()
        return job

    def analysis(self, job, status='ok', age=timedelta(minutes=5)):
        self.session.add(models.JobAnalysis(job_id=job.id, version='v', status=status, source_hash='s',
                                            result_hash='r', payload={}, analysed_at=utcnow() - age))

    def test_counts_postings_and_matches_for_active_jobs_only(self):
        read, old, refreshing, failed, queued, backoff = (
            self.job(name) for name in ('read', 'old', 'refreshing', 'failed', 'queued', 'backoff'))
        closed = self.job('closed', active=False)
        self.analysis(read)
        self.analysis(old, age=timedelta(hours=3))
        self.analysis(refreshing, status='stale')
        self.analysis(failed, status='failed')
        self.analysis(closed)
        later = utcnow() + timedelta(hours=1)
        self.session.add_all([
            models.JobAnalysisQueue(job_id=refreshing.id, profile_set_hash=self.profile_hash, similarity=0.90),
            models.JobAnalysisQueue(job_id=queued.id, profile_set_hash=self.profile_hash, similarity=0.88),
            models.JobAnalysisQueue(job_id=backoff.id, retry_at=later, profile_set_hash=self.profile_hash, similarity=0.85),
            models.JobAnalysisQueue(job_id=closed.id, profile_set_hash=self.profile_hash, similarity=0.95),
        ])
        for job, profile in ((read, self.profile), (old, self.profile), (read, self.other), (closed, self.profile)):
            self.session.add(models.MatchEvidence(profile_id=profile.id, job_id=job.id, version='e', analysis_hash='r',
                                                  units_hash='u', payload={}))
        self.session.add_all([
            models.MatchEvidenceQueue(profile_id=self.profile.id, job_id=old.id),
            models.MatchEvidenceQueue(profile_id=self.profile.id, job_id=refreshing.id),
            models.MatchEvidenceQueue(profile_id=self.profile.id, job_id=failed.id, retry_at=later),
            models.MatchEvidenceQueue(profile_id=self.other.id, job_id=queued.id),
        ])
        self.session.flush()

        progress = journey.analysis_progress(self.session, [self.profile.id])
        self.assertEqual(progress['postings'],
                         {'done': 2, 'queued': 2, 'retrying': 1, 'failed': 1, 'skipped': 0, 'last_hour': 2,
                          'last_day': 3, 'out_of_reach': 0})
        # old is re-queued, so only read counts as done; the other profile's pairs are excluded.
        self.assertEqual(progress['matches'], {'done': 1, 'queued': 2, 'retrying': 1, 'last_hour': 2})

    def test_gated_out_postings_count_as_skipped_not_queued(self):
        eligible = self.job('eligible')
        low_sim = self.job('low-sim')
        too_old = self.job('too-old', date_posted='2020-01-01', first_seen_at=utcnow() - timedelta(days=40))
        old_but_priority = self.job('old-priority', date_posted='2020-01-01', first_seen_at=utcnow() - timedelta(days=40))
        awaiting = self.job('awaiting-embedding')
        self.session.add_all([
            models.JobAnalysisQueue(job_id=eligible.id, profile_set_hash=self.profile_hash, similarity=0.86),
            models.JobAnalysisQueue(job_id=low_sim.id, profile_set_hash=self.profile_hash, similarity=0.60),
            models.JobAnalysisQueue(job_id=old_but_priority.id, priority=True),
            models.JobAnalysisQueue(job_id=awaiting.id),
        ])
        self.session.flush()

        progress = journey.analysis_progress(self.session, [self.profile.id])
        # eligible + old_but_priority are queued; low_sim + too_old are skipped; awaiting is neither.
        self.assertEqual(progress['postings'],
                         {'done': 0, 'queued': 2, 'retrying': 0, 'failed': 0, 'skipped': 2, 'last_hour': 0,
                          'last_day': 0, 'out_of_reach': 0})

    def test_endpoint_validates_profiles(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            journey.get_analysis_progress('x', self.session)
        result = journey.get_analysis_progress(f'{self.profile.id},{self.other.id}', self.session)
        self.assertEqual(result['profile_ids'], [self.profile.id, self.other.id])


if __name__ == '__main__':
    unittest.main()
