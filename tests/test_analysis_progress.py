"""Journey's local matching progress: postings read and profile matches scored."""
import unittest
from datetime import timedelta

from hiring_scraper.app import journey, models
from hiring_scraper.app.models import utcnow
from tests import pg


class AnalysisProgressTests(unittest.TestCase):
    def setUp(self):
        self.session = pg.session(self)
        self.feed = models.JobFeed(
            company=models.Company(source='test', source_id='employer', name='Example Employer'),
            provider='rss', status='parsed', feed_url='https://example.test/jobs')
        self.profile = models.UserProfile(name='Analyst')
        self.other = models.UserProfile(name='Other')
        self.session.add_all([self.feed, self.profile, self.other])
        self.session.flush()

    def job(self, name, active=True):
        job = models.Job(feed=self.feed, external_id=name, title=name, url=f'https://example.test/{name}',
                         description='Text', is_active=active)
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
            models.JobAnalysisQueue(job_id=refreshing.id), models.JobAnalysisQueue(job_id=queued.id),
            models.JobAnalysisQueue(job_id=backoff.id, retry_at=later),
            models.JobAnalysisQueue(job_id=closed.id),
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
                         {'done': 2, 'queued': 2, 'retrying': 1, 'failed': 1, 'last_hour': 2})
        # old is re-queued, so only read counts as done; the other profile's pairs are excluded.
        self.assertEqual(progress['matches'], {'done': 1, 'queued': 2, 'retrying': 1, 'last_hour': 2})

    def test_endpoint_validates_profiles(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            journey.get_analysis_progress('x', self.session)
        result = journey.get_analysis_progress(f'{self.profile.id},{self.other.id}', self.session)
        self.assertEqual(result['profile_ids'], [self.profile.id, self.other.id])


if __name__ == '__main__':
    unittest.main()
