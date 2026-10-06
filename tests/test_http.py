import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from hiring_scraper.http import Client, OriginPacer, RequestBudget

class HttpTests(unittest.TestCase):
    def test_origin_pacer_shares_minimum_spacing_across_client_instances(self):
        pacer=OriginPacer()
        origin=('https','jobs.example')
        with patch('hiring_scraper.http.time.monotonic',side_effect=[10.0,10.0,10.2,10.2]), \
                patch('hiring_scraper.http.time.sleep') as sleep:
            pacer.wait(origin,1.0)
            pacer.wait(origin,1.0)
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0],0.8)

    def test_origin_pacer_does_not_block_an_unrelated_origin_while_waiting(self):
        pacer=OriginPacer()
        slow_origin=('https','slow.example')
        ready_origin=('https','ready.example')
        pacer.last[slow_origin]=9.5
        pacer.last[ready_origin]=9.0
        sleep_entered=Event()
        release_sleep=Event()

        def controlled_sleep(delay):
            self.assertAlmostEqual(delay,0.5)
            sleep_entered.set()
            release_sleep.wait(2)

        with patch('hiring_scraper.http.time.monotonic',return_value=10.0), \
                patch('hiring_scraper.http.time.sleep',side_effect=controlled_sleep):
            with ThreadPoolExecutor(max_workers=2) as pool:
                slow=pool.submit(pacer.wait,slow_origin,1.0)
                self.assertTrue(sleep_entered.wait(1))
                ready=pool.submit(pacer.wait,ready_origin,1.0)
                try:
                    ready.result(timeout=0.25)
                finally:
                    release_sleep.set()
                slow.result(timeout=1)

    def test_shared_request_budget_caps_live_request_claims(self):
        budget=RequestBudget(2)
        self.assertTrue(budget.claim())
        self.assertTrue(budget.claim())
        self.assertFalse(budget.claim())
        self.assertEqual(budget.used,2)

    def test_published_ashby_api_does_not_depend_on_host_robots_endpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            client=Client(folder)
            with patch.object(client,'_raw',return_value=({'state':'ok','status':200},b'{"jobs":[]}')):
                meta,body=client.get_feed('https://api.ashbyhq.com/posting-api/job-board/DeepL')
            self.assertEqual(meta['state'],'ok')
            self.assertEqual(body,b'{"jobs":[]}')

    def test_feed_mode_cannot_fetch_arbitrary_or_write_endpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            client=Client(folder)
            for url in ['https://example.org/private','https://api.ashbyhq.com/jobPosting.list',
                        'https://boards-api.greenhouse.io/v1/boards/a/jobs/123',
                        'https://boards-api.greenhouse.io.evil.example/v1/boards/a/jobs']:
                with self.subTest(url=url): self.assertEqual(client.get_feed(url)[0]['state'],'unsupported_api')

    def test_offline_cache_miss_does_not_attempt_a_live_request(self):
        with tempfile.TemporaryDirectory() as folder:
            client=Client(folder,offline_only=True)
            with patch('hiring_scraper.http.socket.getaddrinfo') as resolve:
                meta,body=client._raw('https://jobs.example/careers')
            resolve.assert_not_called()
            self.assertEqual(meta['state'],'cache_miss')
            self.assertEqual(body,b'')
            self.assertEqual(client.records,[])
