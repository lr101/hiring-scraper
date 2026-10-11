"""Render public, JavaScript-driven job pages without evading access controls."""
from __future__ import annotations

import re
import os
from urllib.parse import urlsplit
from .challenges import is_bot_challenge


BROWSER_REQUEST_LIMIT = 40
BROWSER_CLIENT_REQUEST_BUDGET = BROWSER_REQUEST_LIMIT + 5  # allow the initial six-hop redirect chain
_MAX_HTML_BYTES = 3_000_000
_BLOCKED_RESOURCE_TYPES = {"font", "image", "media", "websocket"}
def render_public_page(client, url: str) -> tuple[dict, bytes]:
    """Fetch HTML, then render it with a bounded headless browser.

    The plain HTTP request is intentional: a 401/403, challenge, or rate limit is
    returned as-is and never followed by a browser attempt. All browser network
    requests are fetched by the client's address-pinned public HTTP transport,
    then supplied to Chromium through Playwright's route handler.
    """
    metadata, initial_body = client.get(url)
    if metadata.get("state") == "bot_challenge":
        return metadata, initial_body
    if metadata.get("state") != "ok" or metadata.get("status") != 200:
        return metadata, initial_body

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {**metadata, "state": "browser_unavailable",
                "error": "Install the Playwright browser dependency to render this page."}, initial_body
    if getattr(os, "geteuid", lambda: 1)() == 0:
        return {**metadata, "state": "browser_unavailable",
                "error": "Run the scanner as an unprivileged user so Chromium can keep its sandbox enabled."}, initial_body

    final_url = metadata.get("final_url") or url
    parsed_final = urlsplit(final_url)
    request_count = 0
    request_error = None
    challenge_detected = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                                                  args=["--disable-dev-shm-usage"])
            try:
                context = browser.new_context(user_agent=client.user_agent, accept_downloads=False,
                                              ignore_https_errors=False, service_workers="block")
                page = context.new_page()
                page.set_default_navigation_timeout(20_000)

                def guard(route):
                    nonlocal request_count, request_error, challenge_detected
                    request_count += 1
                    request = route.request
                    target = request.url
                    if request_count > BROWSER_REQUEST_LIMIT:
                        request_error = "Rendered page exceeded its request limit."
                        route.abort()
                        return
                    if request.resource_type in _BLOCKED_RESOURCE_TYPES:
                        route.abort()
                        return
                    if request.method.upper() != "GET":
                        request_error = "Rendered page attempted an unsupported HTTP method."
                        route.abort()
                        return
                    parts = urlsplit(target)
                    if (parts.scheme not in {"http", "https"} or not parts.hostname or
                            parts.username or parts.password or parts.port not in (None, 80, 443)):
                        request_error = "Rendered page targeted an invalid or unsupported address."
                        route.abort()
                        return
                    allowed_headers = {"accept", "accept-language", "origin", "referer"}
                    headers = {key: value for key, value in request.headers.items()
                               if key.casefold() in allowed_headers}
                    metadata, body = client._raw(target, headers)
                    response_headers = metadata.get("headers") or {}
                    is_main_document = (request.resource_type == "document" and
                                        request.is_navigation_request() and
                                        request.frame.parent_frame is None)
                    content_type = next((value for key, value in response_headers.items()
                                         if key.casefold() == "content-type"), "").casefold()
                    is_html_response = ("text/html" in content_type or
                                        "application/xhtml+xml" in content_type)
                    marker_present = is_bot_challenge(body)
                    if ((metadata.get("cf_mitigated") or "").casefold() == "challenge" or
                            (is_main_document and marker_present) or
                            (is_html_response and marker_present)):
                        challenge_detected = True
                    if metadata.get("status") is None or metadata.get("state") in {
                            "network_error", "body_too_large", "request_budget_exhausted", "origin_backoff"}:
                        request_error = metadata.get("error") or metadata.get("state") or "Browser request failed."
                        route.abort()
                        return
                    route.fulfill(status=metadata["status"], headers=response_headers, body=body)

                page.route("**/*", guard)
                page.route_web_socket("**/*", lambda websocket: websocket.close(
                    code=1008, reason="WebSocket access is disabled for public job-page rendering."))
                response = page.goto(final_url, wait_until="domcontentloaded", timeout=20_000)
                if response is None:
                    return {**metadata, "state": "browser_error", "error": "The browser received no page response."}, b""
                response_headers = response.headers
                status = response.status
                content = page.content()
                if challenge_detected or is_bot_challenge(content, response_headers.get("cf-mitigated")):
                    return {**metadata, "state": "bot_challenge", "status": status,
                            "error": "The rendered page is a bot challenge."}, b""
                if status == 429:
                    return {**metadata, "state": "throttled", "status": status,
                            "error": "The source rate-limited the browser request."}, b""
                if status in {401, 403}:
                    return {**metadata, "state": "blocked", "status": status,
                            "error": f"The source denied the browser request (HTTP {status})."}, b""
                if status >= 400:
                    return {**metadata, "state": "http_error", "status": status,
                            "error": f"The browser received HTTP {status}."}, b""
                rendered_url = page.url
                if (urlsplit(rendered_url).hostname or "").casefold() != (parsed_final.hostname or "").casefold():
                    return {**metadata, "state": "browser_error", "status": status,
                            "error": "The rendered page redirected to a different host."}, b""
                body = content.encode("utf-8")
                if len(body) > _MAX_HTML_BYTES:
                    return {**metadata, "state": "body_too_large", "status": status,
                            "error": "The rendered page exceeded the 3 MB capture limit."}, b""
                if request_error:
                    return {**metadata, "state": "browser_error", "status": status,
                            "error": request_error}, b""
                # Let client-side job-board widgets finish their first data load.
                try:
                    page.wait_for_load_state("networkidle", timeout=4_000)
                    body = page.content().encode("utf-8")
                except Exception:
                    pass
                if request_error:
                    return {**metadata, "state": "browser_error", "status": status,
                            "error": request_error}, b""
                if challenge_detected or is_bot_challenge(body, response_headers.get("cf-mitigated")):
                    return {**metadata, "state": "bot_challenge", "status": status,
                            "error": "The rendered page is a bot challenge."}, b""
                if len(body) > _MAX_HTML_BYTES:
                    return {**metadata, "state": "body_too_large", "status": status,
                            "error": "The rendered page exceeded the 3 MB capture limit."}, b""
                return {**metadata, "state": "ok", "status": status, "final_url": rendered_url,
                        "rendered": True, "browser_requests": request_count,
                        "cf_mitigated": response_headers.get("cf-mitigated")}, body
            finally:
                browser.close()
    except Exception as error:
        message = re.sub(r"\s+", " ", str(error)).strip()[:500]
        return {**metadata, "state": "browser_error",
                "error": message or "The headless browser could not render the page."}, b""
