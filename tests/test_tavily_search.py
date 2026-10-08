import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from hiring_scraper import website_discovery as websites


class Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, limit): return json.dumps(self.payload).encode()


class TavilySearchTests(unittest.TestCase):
    def test_uses_basic_search_and_returns_bounded_url_leads_without_content(self):
        requests = []
        def opener(request, timeout):
            requests.append(request)
            return Response({'results': [
                {'url': 'https://northstar.de/', 'title': 'Northstar', 'content': 'Long snippet'},
                {'title': 'missing URL'}, {'url': None},
                *[{'url': f'https://company-{i}.de/', 'title': str(i)} for i in range(6)]],
                'usage': {'credits': 1}})
        provider = websites.TavilySearch('tavily-test-token', opener=opener)
        results = provider('Northstar Karlsruhe')
        self.assertEqual(len(results), 5)
        self.assertEqual(results[0], {'url': 'https://northstar.de/', 'title': 'Northstar'})
        request = requests[0]
        self.assertEqual(request.full_url, 'https://api.tavily.com/search')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer tavily-test-token')
        payload = json.loads(request.data)
        self.assertEqual(payload['query'], 'Northstar Karlsruhe')
        self.assertEqual(payload['search_depth'], 'basic')
        self.assertFalse(payload['auto_parameters'])
        self.assertFalse(payload['include_answer'])
        self.assertFalse(payload['include_raw_content'])
        self.assertEqual(payload['country'], 'germany')
        self.assertEqual(provider.credits_used, 1)

    def test_provider_errors_disable_further_requests_and_do_not_log_credentials(self):
        for status in (401, 429, 432, 500):
            with self.subTest(status=status):
                requests = []
                def opener(request, timeout):
                    requests.append(request)
                    raise HTTPError(request.full_url, status, 'tavily-test-token', {}, None)
                provider = websites.TavilySearch('tavily-test-token', opener=opener)
                with self.assertLogs(websites.LOG, level='WARNING') as logs:
                    self.assertEqual(provider('first'), [])
                self.assertEqual(provider('second'), [])
                self.assertEqual(len(requests), 1)
                self.assertNotIn('tavily-test-token', ' '.join(logs.output))

    def test_tavily_key_takes_precedence_and_brave_remains_optional(self):
        for environment, kind in [
                ({'TAVILY_API_KEY': ' tavily-test-token ', 'BRAVE_SEARCH_API_KEY': 'brave-test'}, websites.TavilySearch),
                ({'BRAVE_SEARCH_API_KEY': 'brave-test'}, websites.BraveSearch), ({}, type(None))]:
            with self.subTest(environment=environment), patch.dict('os.environ', environment, clear=True):
                self.assertIsInstance(websites.configured_website_search(), kind)

    def test_malformed_provider_response_fails_closed(self):
        for payload in ([], {'results': {}}, {'results': 'not a list'}):
            with self.subTest(payload=payload):
                provider = websites.TavilySearch('tavily-test-token', opener=lambda *a, **k: Response(payload))
                self.assertEqual(provider('query'), [])
                self.assertTrue(provider.disabled)


if __name__ == '__main__':
    unittest.main()
