"""Posting analysis on job details without an application profile."""
import unittest

from hiring_scraper.app import api, models
from hiring_scraper.app.enrichment import current_enrichment
from hiring_scraper.job_analysis import VERSION, analysis_payload, result_hash, source_hash
from tests import pg


class JobDetailAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.session = pg.session(self)
        feed = models.JobFeed(
            company=models.Company(source='test', source_id='employer', name='Example Employer'),
            provider='rss', status='parsed', feed_url='https://example.test/jobs',
        )
        self.job = models.Job(
            feed=feed, external_id='role', title='Archivist', url='https://example.test/role',
            description='Catalogue new acquisitions. Archival training required. Latin is a plus.',
        )
        self.session.add(self.job)
        self.session.flush()

    def save_analysis(self, status):
        lines = ['Job title: Archivist', 'Catalogue new acquisitions.', 'Archival training required.', 'Latin is a plus.']
        payload = {} if status == 'failed' else analysis_payload(
            lines, 'OCC archival science\nIND museums\nDUTIES\nD 1 | Catalogue new acquisitions.\n'
            'REQUIREMENTS\nR 2 must education | Archival training required.\nR 3 nice language | Latin is a plus.\nEND',
            ['title', 'neutral', 'neutral', 'neutral'],
        )
        self.session.add(models.JobAnalysis(
            job_id=self.job.id, version=VERSION, status=status, source_hash=source_hash(lines),
            result_hash=result_hash(VERSION, payload) if payload else '', payload=payload,
            error='Internal worker error' if status == 'failed' else None,
        ))

    def detail(self):
        self.session.flush()
        return api.get_job(self.job.id, self.session)

    def assert_ready_analysis(self):
        detail = self.detail()
        self.assertEqual(detail.get('analysis_state'), 'ready')
        self.assertNotIn('profile_match', detail)
        self.assertEqual(detail['analysis'], {
            'occupation': 'archival science',
            'industry': 'museums',
            'duties': [{'line': 1, 'text': 'Catalogue new acquisitions.'}],
            'requirements': [
                {'line': 2, 'text': 'Archival training required.', 'need': 'must', 'kind': 'education'},
                {'line': 3, 'text': 'Latin is a plus.', 'need': 'nice', 'kind': 'language'},
            ],
        })

    def test_ready_analysis_exposes_posting_evidence_only(self):
        self.save_analysis('ok')
        self.assert_ready_analysis()

    def test_stale_analysis_stays_ready_during_refresh(self):
        self.save_analysis('stale')
        self.session.add(models.JobAnalysisQueue(job_id=self.job.id))
        self.assert_ready_analysis()

    def test_queued_posting_without_analysis_is_pending(self):
        self.session.add(models.JobAnalysisQueue(job_id=self.job.id))
        detail = self.detail()
        self.assertEqual(detail.get('analysis_state'), 'pending')
        self.assertIsNone(detail['analysis'])

    def test_failed_analysis_is_reported_while_waiting_for_retry(self):
        self.save_analysis('failed')
        self.session.add(models.JobAnalysisQueue(job_id=self.job.id))
        detail = self.detail()
        self.assertEqual(detail.get('analysis_state'), 'failed')
        self.assertIsNone(detail['analysis'])
        self.assertNotIn('Internal worker error', str(detail))

    def test_posting_without_analysis_or_queue_is_unavailable(self):
        detail = self.detail()
        self.assertEqual(detail.get('analysis_state'), 'unavailable')
        self.assertIsNone(detail['analysis'])

    def test_stored_enrichment_omits_only_the_legacy_vocabulary_warning(self):
        warning = 'No skills recognized in the current vocabulary'
        self.job.enrichment = {**current_enrichment(self.job), 'warnings': [warning, 'Salary not stated']}
        detail = self.detail()
        self.assertEqual(detail['enrichment']['warnings'], ['Salary not stated'])
        self.assertIn(warning, self.job.enrichment['warnings'])
