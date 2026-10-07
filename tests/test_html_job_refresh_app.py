import unittest
from unittest.mock import Mock, patch

from hiring_scraper.app.models import JobFeed
from hiring_scraper.app.worker import _request


class HtmlJobRefreshTests(unittest.TestCase):
    def test_html_job_refresh_uses_robots_aware_page_fetch(self):
        client = Mock()
        client.get.return_value = ({'state': 'ok'}, b'<html></html>')
        feed = JobFeed(provider='html_jobs', feed_url='https://example.test/careers/')
        with patch('hiring_scraper.app.worker._pace_origin'), \
             patch('hiring_scraper.app.worker.Client', return_value=client):
            metadata, body = _request(feed)
        self.assertEqual(metadata['state'], 'ok')
        self.assertTrue(body.startswith(b'<html'))
        client.get.assert_called_once_with(feed.feed_url)
        client.get_feed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
