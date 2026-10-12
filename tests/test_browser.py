import sys
import types
import unittest
from unittest.mock import patch

from hiring_scraper.browser import render_public_page


class BrowserTransportTests(unittest.TestCase):
    def test_all_browser_requests_use_the_pinned_transport_without_disabling_sandbox(self):
        page_url = "https://jobs.example/careers"
        xhr_url = "https://api.example/jobs"
        script_urls = [f"https://cdn.example/jobs-{index}.js" for index in range(10)]
        html = b"<html><script src='https://cdn.example/jobs.js'></script></html>"

        class FakeClient:
            user_agent = "HiringScraper test"

            def __init__(self):
                self.calls = []

            def get(self, url):
                return {"state": "ok", "status": 200, "final_url": url}, html

            def _raw(self, url, headers):
                self.calls.append((url, headers))
                if url == xhr_url:
                    return {"state": "ok", "status": 403,
                            "headers": {"content-type": "text/html; charset=utf-8"}}, \
                        b"<!doctype html><title>Verify you are human</title>"
                body = html if url == page_url else b"window.jobs = []; // verify you are human"
                content_type = "text/html" if url == page_url else "application/javascript"
                return {"state": "ok", "status": 200,
                        "headers": {"content-type": content_type}}, body

        class Request:
            def __init__(self, url, resource_type="document", method="GET"):
                self.url = url
                self.resource_type = resource_type
                self.method = method
                self.headers = {"accept": "text/html", "cookie": "must-not-forward"}
                self.frame = types.SimpleNamespace(parent_frame=None if resource_type == "document" else object())

            def is_navigation_request(self):
                return self.resource_type == "document"

        class Route:
            def __init__(self, request):
                self.request = request
                self.response = None
                self.aborted = False

            def fulfill(self, **response):
                self.response = response

            def abort(self):
                self.aborted = True

        class Response:
            status = 200
            headers = {"content-type": "text/html"}

        class Page:
            url = page_url

            def __init__(self):
                self.late_method = None
                self.trigger_xhr = False

            def route(self, _pattern, handler):
                self.handler = handler

            def route_web_socket(self, _pattern, handler):
                self.websocket_handler = handler

            def set_default_navigation_timeout(self, _timeout):
                pass

            def goto(self, _url, **_options):
                self.document_route = Route(Request(page_url))
                self.handler(self.document_route)
                self.asset_routes = []
                for script_url in script_urls:
                    route = Route(Request(script_url, "script"))
                    self.handler(route)
                    self.asset_routes.append(route)
                self.image_route = Route(Request("https://jobs.example/logo.png", "image"))
                self.handler(self.image_route)
                if self.trigger_xhr:
                    self.xhr_route = Route(Request(xhr_url, "xhr"))
                    self.handler(self.xhr_route)
                self.websocket = types.SimpleNamespace(close=lambda **kwargs: setattr(self, "websocket_close", kwargs))
                self.websocket_handler(self.websocket)
                return Response()

            def content(self):
                return html.decode("utf-8")

            def wait_for_load_state(self, *_args, **_kwargs):
                if self.late_method:
                    self.late_route = Route(Request("https://api.example/jobs", "xhr", self.late_method))
                    self.handler(self.late_route)
                else:
                    raise TimeoutError("No further events")

        page = Page()

        class Context:
            def new_page(self):
                return page

        class Browser:
            def new_context(self, **_options):
                return Context()

            def close(self):
                pass

        launch_calls = []

        class Chromium:
            def launch(self, **options):
                launch_calls.append(options)
                return Browser()

        class Manager:
            def __enter__(self):
                return types.SimpleNamespace(chromium=Chromium())

            def __exit__(self, *_args):
                return False
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = lambda: Manager()
        playwright = types.ModuleType("playwright")
        playwright.__path__ = []
        client = FakeClient()

        with patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}), \
                patch("hiring_scraper.browser.os.geteuid", return_value=10001):
            metadata, body = render_public_page(client, page_url)

        self.assertEqual(metadata["state"], "ok")
        self.assertTrue(metadata["rendered"])
        self.assertEqual(body, html)
        self.assertEqual(len(client.calls), 11)
        self.assertEqual([url for url, _headers in client.calls], [page_url, *script_urls])
        self.assertNotIn("cookie", client.calls[0][1])
        self.assertTrue(page.document_route.response)
        self.assertTrue(all(route.response for route in page.asset_routes))
        self.assertTrue(page.image_route.aborted)
        self.assertEqual(page.websocket_close["code"], 1008)
        self.assertTrue(launch_calls[0]["chromium_sandbox"])
        self.assertNotIn("--no-sandbox", launch_calls[0]["args"])

        page.late_method = "HEAD"
        with patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}), \
                patch("hiring_scraper.browser.os.geteuid", return_value=10001):
            late_metadata, late_body = render_public_page(client, page_url)
        self.assertEqual(late_metadata["state"], "browser_error")
        self.assertIn("unsupported HTTP method", late_metadata["error"])
        self.assertTrue(page.late_route.aborted)
        self.assertEqual(late_body, b"")

        page.late_method = None
        page.trigger_xhr = True
        with patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}), \
                patch("hiring_scraper.browser.os.geteuid", return_value=10001):
            challenge_metadata, challenge_body = render_public_page(client, page_url)
        self.assertEqual(challenge_metadata["state"], "bot_challenge")
        self.assertEqual(challenge_body, b"")
        self.assertTrue(page.xhr_route.response)

    def test_root_process_fails_closed_instead_of_disabling_chromium_sandbox(self):
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = lambda: (_ for _ in ()).throw(AssertionError("Chromium must not launch as root"))
        playwright = types.ModuleType("playwright")
        playwright.__path__ = []
        client = types.SimpleNamespace(user_agent="HiringScraper test",
                                       get=lambda url: ({"state": "ok", "status": 200,
                                                         "final_url": url}, b"<html></html>"))
        with patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}), \
                patch("hiring_scraper.browser.os.geteuid", return_value=0):
            metadata, body = render_public_page(client, "https://jobs.example/careers")
        self.assertEqual(metadata["state"], "browser_unavailable")
        self.assertIn("sandbox", metadata["error"])
        self.assertEqual(body, b"<html></html>")


if __name__ == "__main__":
    unittest.main()
