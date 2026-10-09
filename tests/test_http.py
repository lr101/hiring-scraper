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

class PublicCrawlTests(unittest.TestCase):
    def test_explicit_robots_skip_follows_public_redirect_without_policy_request(self):
        records = {'https://example.org/': ({'status': 302, 'state': 'http_error', 'location': '/jobs'}, b''),
                   'https://example.org/jobs': ({'status': 200, 'state': 'ok'}, b'Jobs')}
        with tempfile.TemporaryDirectory() as folder:
            client = Client(folder, respect_robots=False)
            def request(url):
                self.assertIn(url, records)
                return records[url]
            with patch.object(client, '_raw', side_effect=request):
                meta, body = client.get('https://example.org/')
            self.assertEqual(body, b'Jobs')
            self.assertEqual(meta['final_url'], 'https://example.org/jobs')
            self.assertEqual(meta['access_mode'], 'public_without_robots')

    def test_robots_skip_retains_private_destination_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client(folder, respect_robots=False, delay=0)
            with patch('hiring_scraper.http.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
                meta, body = client.get('http://localhost/private')
            self.assertEqual(meta['state'], 'network_error')
            self.assertIn('Non-public', meta['error'])
            self.assertEqual(body, b'')

    def test_retry_after_pauses_other_clients_on_same_origin_only(self):
        import io
        from email.message import Message
        class Response(io.BytesIO):
            code = 429
            headers = Message()
        response = Response(b'Busy')
        response.headers['Retry-After'] = '120'
        pacer = OriginPacer()
        with tempfile.TemporaryDirectory() as folder:
            first = Client(folder+'/one', origin_pacer=pacer, delay=0)
            second = Client(folder+'/two', origin_pacer=pacer, delay=0)
            with patch('hiring_scraper.http.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]), patch.object(first.opener, 'open', return_value=response), patch('hiring_scraper.http.time.monotonic', return_value=10.0):
                self.assertEqual(first._raw('https://example.org/jobs')[0]['status'], 429)
                meta, body = second._raw('https://example.org/another')
                self.assertEqual(meta['state'], 'origin_backoff')
                self.assertEqual(meta['retry_after_seconds'], 120)
                self.assertEqual(body, b'')
                self.assertIsNone(pacer.backoff_remaining(('https', 'other.example')))

    def test_queued_origin_request_observes_cooldown_set_while_waiting(self):
        import io
        from email.message import Message
        class Response(io.BytesIO):
            code = 429
            headers = Message()
        response = Response(b'Busy')
        response.headers['Retry-After'] = '120'
        network_started, release_network = Event(), Event()
        queued, release_queue = Event(), Event()
        pacer = OriginPacer()
        def open_response(*args, **kwargs):
            network_started.set()
            release_network.wait(2)
            return response
        def sleep(delay):
            queued.set()
            release_queue.wait(2)
        with tempfile.TemporaryDirectory() as folder:
            first = Client(folder+'/first', origin_pacer=pacer, delay=1)
            second = Client(folder+'/second', origin_pacer=pacer, delay=1)
            with patch('hiring_scraper.http.time.monotonic',return_value=10.0), patch('hiring_scraper.http.time.sleep',side_effect=sleep), patch('hiring_scraper.http.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]), patch.object(first.opener,'open',side_effect=open_response), patch.object(second.opener,'open',side_effect=AssertionError('Request sent during cooldown')):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    a=pool.submit(first._raw,'https://example.org/a')
                    self.assertTrue(network_started.wait(1))
                    b=pool.submit(second._raw,'https://example.org/b')
                    try:
                        self.assertTrue(queued.wait(1))
                        release_network.set()
                        self.assertEqual(a.result(timeout=1)[0]['status'],429)
                        release_queue.set()
                        self.assertEqual(b.result(timeout=1)[0]['state'],'origin_backoff')
                    finally:
                        release_network.set();release_queue.set()
