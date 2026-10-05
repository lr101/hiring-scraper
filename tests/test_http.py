import tempfile
import unittest
from unittest.mock import patch
from hiring_scraper.http import Client

class HttpTests(unittest.TestCase):
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
