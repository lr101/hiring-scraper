"""Render public, JavaScript-driven job pages without evading access controls."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit
from .challenges import is_bot_challenge


_MAX_BROWSER_REQUESTS = 40
_MAX_HTML_BYTES = 3_000_000
_BLOCKED_RESOURCE_TYPES = {"font", "image", "media", "websocket"}
def _public_http_url(value: str) -> tuple[str, str] | None:
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or
                parts.username or parts.password or parts.port not in (None, 80, 443)):
            return None
        host = parts.hostname.rstrip(".").casefold()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
            return None
        addresses = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            return None
        return parts.scheme, parts.netloc.casefold()
    except (OSError, ValueError, TypeError):
        return None


def render_public_page(client, url: str) -> tuple[dict, bytes]:
    """Fetch HTML, then render it with a bounded headless browser.

    The plain HTTP request is intentional: a 401/403, challenge, or rate limit is
    returned as-is and never followed by a browser attempt. Browser requests are
    also restricted to public HTTP(S) addresses and a small request budget.
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

    final_url = metadata.get("final_url") or url
    parsed_final = urlsplit(final_url)
    request_count = 0
    request_error = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                context = browser.new_context(user_agent=client.user_agent, accept_downloads=False,
                                              ignore_https_errors=False, service_workers="block")
                page = context.new_page()
                page.set_default_navigation_timeout(20_000)

                def guard(route):
                    nonlocal request_count, request_error
                    request_count += 1
                    target = route.request.url
                    if request_count > _MAX_BROWSER_REQUESTS:
                        request_error = "Rendered page exceeded its request limit."
                        route.abort()
                        return
                    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                        route.abort()
                        return
                    origin = _public_http_url(target)
                    if origin is None:
                        request_error = "A browser request targeted a non-public or invalid address."
                        route.abort()
                        return
                    if client.origin_pacer is not None:
                        remaining = client.origin_pacer.wait(origin, client.delay)
                        if remaining is not None:
                            request_error = "A browser request target is under origin backoff."
                            route.abort()
                            return
                    route.continue_()

                page.route("**/*", guard)
                response = page.goto(final_url, wait_until="domcontentloaded", timeout=20_000)
                if response is None:
                    return {**metadata, "state": "browser_error", "error": "The browser received no page response."}, b""
                response_headers = response.headers
                status = response.status
                content = page.content()
                if is_bot_challenge(content, response_headers.get("cf-mitigated")):
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
                if is_bot_challenge(body, response_headers.get("cf-mitigated")):
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
