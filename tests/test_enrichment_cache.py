"""Rendering one job repeatedly must not repeat expensive requirement extraction."""
import unittest
from unittest.mock import patch

from hiring_scraper.app import enrichment, models


class EnrichmentCacheTests(unittest.TestCase):
    def setUp(self):
        cache = patch.dict(enrichment._ENRICHMENT_CACHE, {}, clear=True)
        cache.start()
        self.addCleanup(cache.stop)

    def test_repeated_reads_reuse_extraction_and_changed_sources_recompute(self):
        job = models.Job(title='Developer', url='https://example.org/jobs/1',
                         description='Python development.', raw_metadata={}, locations=[])
        with patch.object(enrichment, 'enrich_job', wraps=enrichment.enrich_job) as extract:
            first = enrichment.current_enrichment(job)
            second = enrichment.current_enrichment(job)
            self.assertEqual([row['name'] for row in first['skills']], ['Python'])
            self.assertEqual(second, first)
            self.assertEqual(extract.call_count, 1, 'One job page must extract requirements just once')
            job.description = 'Java development.'
            changed = enrichment.current_enrichment(job)
            self.assertEqual([row['name'] for row in changed['skills']], ['Java'])
            self.assertNotEqual(changed['source_hash'], first['source_hash'])
            self.assertEqual(extract.call_count, 2)
            self.assertIsNone(job.enrichment, 'Reading should not dirty the persisted job')

    def test_distinct_job_instances_share_extraction_for_the_same_verified_source(self):
        values = dict(title='Developer', url='https://example.org/cache-shared',
                      description='Python development.', raw_metadata={}, locations=[])
        first = models.Job(**values)
        second = models.Job(**values)
        with patch.object(enrichment, 'enrich_job', wraps=enrichment.enrich_job) as extract:
            one = enrichment.current_enrichment(first)
            two = enrichment.current_enrichment(second)
            self.assertEqual(one, two)
            self.assertEqual(extract.call_count, 1, 'Separate page requests must reuse identical extraction')

    def test_concurrent_reads_extract_the_same_source_once(self):
        from concurrent.futures import Future, ThreadPoolExecutor
        from threading import Event
        started, waiting, release = Event(), Event(), Event()
        class ObservedFuture(Future):
            def result(self, timeout=None):
                waiting.set()
                return super().result(timeout)
        real_extract = enrichment.enrich_job
        def slow_extract(source):
            started.set()
            self.assertTrue(release.wait(5))
            return real_extract(source)
        def read():
            return enrichment.current_enrichment(models.Job(title='Engineer',
                url='https://example.org/cache-concurrent', description='Python development.',
                raw_metadata={}, locations=[]))
        with patch.object(enrichment, 'Future', ObservedFuture), patch.object(enrichment, 'enrich_job', side_effect=slow_extract) as extract:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(read)
                self.assertTrue(started.wait(5))
                second = executor.submit(read)
                try:
                    self.assertTrue(waiting.wait(5), 'Second reader must wait on the in-flight extraction')
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=5), second.result(timeout=5))
            self.assertEqual(extract.call_count, 1)

    def test_concurrent_extraction_failures_reach_waiters_and_allow_retry(self):
        from concurrent.futures import Future, ThreadPoolExecutor
        from threading import Event
        started, waiting, release = Event(), Event(), Event()
        class ObservedFuture(Future):
            def result(self, timeout=None):
                waiting.set()
                return super().result(timeout)
        def fail_extract(source):
            started.set()
            self.assertTrue(release.wait(5))
            raise ValueError('malformed source')
        def read():
            return enrichment.current_enrichment(models.Job(title='Engineer',
                url='https://example.org/cache-concurrent-retry', description='Python development.',
                raw_metadata={}, locations=[]))
        with patch.object(enrichment, 'Future', ObservedFuture), patch.object(enrichment, 'enrich_job', side_effect=fail_extract) as extract:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(read)
                self.assertTrue(started.wait(5))
                second = executor.submit(read)
                try:
                    self.assertTrue(waiting.wait(5))
                finally:
                    release.set()
                for result in (first, second):
                    with self.assertRaisesRegex(ValueError, 'malformed source'):
                        result.result(timeout=5)
            self.assertEqual(extract.call_count, 1)
        self.assertEqual([row['name'] for row in read()['skills']], ['Python'])

    def test_shared_results_are_isolated_and_extraction_failures_can_retry(self):
        values = dict(title='Developer', url='https://example.org/cache-retry',
                      description='Python development.', raw_metadata={}, locations=[])
        real_extract = enrichment.enrich_job
        with patch.object(enrichment, 'enrich_job', side_effect=ValueError('malformed source')):
            with self.assertRaisesRegex(ValueError, 'malformed source'):
                enrichment.current_enrichment(models.Job(**values))
        first = enrichment.current_enrichment(models.Job(**values))
        first['skills'].clear()
        with patch.object(enrichment, 'enrich_job', wraps=real_extract) as extract:
            second = enrichment.current_enrichment(models.Job(**values))
            self.assertEqual([row['name'] for row in second['skills']], ['Python'])
            self.assertEqual(extract.call_count, 0)

    def test_shared_cache_evicts_old_sources_at_its_bound(self):
        def read(number):
            return enrichment.current_enrichment(models.Job(title='Developer',
                url=f'https://example.org/cache-eviction/{number}', description='Python development.',
                raw_metadata={}, locations=[]))
        with patch.object(enrichment, '_ENRICHMENT_CACHE_SIZE', 1), patch.object(enrichment, 'enrich_job', wraps=enrichment.enrich_job) as extract:
            read(1); read(2); read(1)
            self.assertEqual(extract.call_count, 3)
            self.assertEqual(len(enrichment._ENRICHMENT_CACHE), 1)

    def test_repeated_fixture_sized_scans_reuse_extraction(self):
        # The seeded Journey inventory exceeds 1,600 jobs. An undersized LRU
        # would evict every entry just before its reuse on the next ordered scan.
        def scan():
            for number in range(1675):
                enrichment.current_enrichment(models.Job(title='Developer',
                    url=f'https://example.org/cache-inventory/{number}', description='Python development.',
                    raw_metadata={}, locations=[]))
        with patch.object(enrichment, 'enrich_job', wraps=enrichment.enrich_job) as extract:
            scan(); scan()
            self.assertEqual(extract.call_count, 1675)
