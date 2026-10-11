"""Bounded HTTP capture with per-origin pacing and bot-challenge detection."""
import hashlib
import ipaddress
import json
import re
import http.client
import socket
import time
from threading import Lock
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler
from hiring_scraper.challenges import is_bot_challenge

UA = 'HiringDiscoveryResearch/0.2 (career discovery feasibility study)'


class NoRedirect(HTTPRedirectHandler):
    """Keep homepage search callers from following redirects implicitly."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_CAPTURED_RESPONSE_HEADERS = {
    'access-control-allow-credentials', 'access-control-allow-headers',
    'access-control-allow-methods', 'access-control-allow-origin', 'cache-control',
    'cf-mitigated', 'content-encoding', 'content-language', 'content-type',
    'etag', 'expires', 'last-modified', 'location', 'retry-after', 'vary',
}


def _resolve_public_addresses(host: str, port: int) -> list[tuple[int, int, int, tuple, str]]:
    """Resolve once, reject mixed/private answers, and retain numeric socket targets."""
    if '%' in host:
        raise ValueError('Scoped IP destinations are not accepted')
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    else:
        if not literal.is_global:
            raise ValueError('Non-public destination rejected')
        family = socket.AF_INET if literal.version == 4 else socket.AF_INET6
        sockaddr = (str(literal), port) if family == socket.AF_INET else (str(literal), port, 0, 0)
        results = [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr)]

    addresses = []
    seen = set()
    for family, socktype, proto, _canonname, sockaddr in results:
        address = ipaddress.ip_address(str(sockaddr[0]).split('%', 1)[0])
        if not address.is_global:
            raise ValueError('Non-public destination rejected')
        key = (family, socktype, proto, tuple(sockaddr), str(address))
        if key not in seen:
            seen.add(key)
            addresses.append(key)
    if not addresses:
        raise ValueError('No public destination addresses were returned')
    return addresses


def _connect_public(addresses: list[tuple[int, int, int, tuple, str]], timeout: float,
                    source_address=None) -> socket.socket:
    """Connect to an already validated numeric address without resolving its name again."""
    failures = []
    for family, socktype, proto, sockaddr, expected_ip in addresses:
        connection = socket.socket(family, socktype, proto)
        try:
            connection.settimeout(timeout)
            if source_address is not None:
                connection.bind(source_address)
            connection.connect(sockaddr)
            peer = ipaddress.ip_address(str(connection.getpeername()[0]).split('%', 1)[0])
            if peer != ipaddress.ip_address(expected_ip) or not peer.is_global:
                raise OSError('Connected peer did not match the validated public address')
            return connection
        except OSError as error:
            failures.append(error)
            connection.close()
    if failures:
        raise failures[-1]
    raise OSError('No public destination addresses were available')


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, addresses, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self._addresses = addresses

    def connect(self):
        self.sock = _connect_public(self._addresses, self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, addresses, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self._addresses = addresses

    def connect(self):
        sock = _connect_public(self._addresses, self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


class OriginPacer:
    """Pace requests per origin without making unrelated origins wait on each other."""
    def __init__(self):
        self.last: dict[tuple[str, str], float] = {}
        self._locks_guard = Lock()
        self._backoff = {}
        self._origin_locks: dict[tuple[str, str], Lock] = {}

    def wait(self, origin: tuple[str, str], delay: float) -> None:
        with self._locks_guard:
            origin_lock = self._origin_locks.setdefault(origin, Lock())
        with origin_lock:
            remaining = self.backoff_remaining(origin)
            if remaining is not None:
                return remaining
            now = time.monotonic()
            wait = delay - (now - self.last.get(origin, 0.0))
            if wait > 0:
                time.sleep(wait)
            remaining = self.backoff_remaining(origin)
            if remaining is not None:
                return remaining
            self.last[origin] = time.monotonic()

    def defer(self, origin, retry_after=None):
        seconds = 60.0
        if retry_after:
            try:
                seconds = max(1.0, float(retry_after))
            except (ValueError, TypeError):
                try:
                    moment = parsedate_to_datetime(retry_after)
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=timezone.utc)
                    seconds = max(1.0, (moment - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        if seconds != seconds or seconds == float('inf'):
            seconds = 60.0
        with self._locks_guard:
            self._backoff[origin] = max(self._backoff.get(origin, 0), time.monotonic() + seconds)

    def backoff_remaining(self, origin):
        with self._locks_guard:
            until = self._backoff.get(origin)
            if until is None:
                return None
            remaining = until - time.monotonic()
        return remaining if remaining > 0 else None


class RequestBudget:
    """Thread-safe cap on live HTTP requests shared by a bounded crawl run."""
    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self.lock = Lock()

    def claim(self) -> bool:
        with self.lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True

class Client:
    def __init__(self, out, timeout=12, delay=1.0, max_requests=300, cache_from=None, user_agent=UA,
                 origin_pacer: OriginPacer | None = None, request_budget: RequestBudget | None = None,
                 offline_only=False):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.timeout, self.delay, self.max_requests = timeout, delay, max_requests
        self.user_agent = user_agent
        self.origin_pacer = origin_pacer
        self.request_budget = request_budget
        self.offline_only = offline_only
        self.cache, self.last, self.records = {}, {}, []
        self.used = set()
        if cache_from:
            for path in Path(cache_from).glob('*.json'):
                record = json.loads(path.read_text())
                body = path.with_suffix('.body').read_bytes()
                if hashlib.sha256(body).hexdigest() != record['sha256']:
                    raise ValueError('Corrupt cache capture: '+str(path))
                self.cache[record['url']] = ({**record,'reused_from':str(path)},body)

    def _open_pinned_response(self, url, addresses, request_headers=None):
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        connection_type = _PinnedHTTPSConnection if parts.scheme == 'https' else _PinnedHTTPConnection
        connection = connection_type(parts.hostname, port, addresses, self.timeout)
        path = parts.path or '/'
        if parts.query:
            path += '?' + parts.query
        headers = {'Host': parts.netloc, 'User-Agent': self.user_agent, 'Connection': 'close'}
        headers.update(request_headers or {})
        headers['Host'] = parts.netloc
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            body = response.read(3_000_001)
            response_headers = {}
            for name, value in response.getheaders():
                key = name.casefold()
                if key in _CAPTURED_RESPONSE_HEADERS:
                    response_headers[key] = value
            return response.status, response_headers, body
        finally:
            connection.close()

    def _raw(self, url, request_headers=None):
        if url in self.cache:
            record, body = self.cache[url]
            if url not in self.used:
                self.used.add(url)
                self.records.append(record)
                key = record['capture']
                (self.out/(key+'.body')).write_bytes(body)
                (self.out/(key+'.json')).write_text(json.dumps(record,indent=2)+'\n')
            return record, body
        if self.offline_only:
            return {'url': url, 'state': 'cache_miss'}, b''
        parts = urlsplit(url)
        origin = (parts.scheme, parts.netloc.lower())
        remaining = self.origin_pacer.backoff_remaining(origin) if self.origin_pacer is not None else None
        if remaining is not None:
            return {'url':url, 'state':'origin_backoff', 'retry_after_seconds':remaining}, b''
        record = {'url':url, 'checked_at':datetime.now(timezone.utc).isoformat(), 'user_agent':self.user_agent}
        body = b''
        start = time.monotonic()
        try:
            if len(self.records) >= self.max_requests:
                return {'url':url, 'state':'request_budget_exhausted'}, body
            p = urlsplit(url)
            if p.scheme not in {'https','http'} or not p.hostname or p.username or p.password:
                raise ValueError('Only public HTTP(S) URLs without credentials are accepted')
            if self.request_budget is not None and not self.request_budget.claim():
                return {'url':url,'state':'request_budget_exhausted'}, body
            addresses = _resolve_public_addresses(p.hostname, p.port or (443 if p.scheme=='https' else 80))
            origin = (p.scheme,p.netloc.lower())
            if self.origin_pacer is not None:
                remaining = self.origin_pacer.wait(origin,self.delay)
                if remaining is not None:
                    return {'url':url, 'state':'origin_backoff', 'retry_after_seconds':remaining}, b''
            else:
                time.sleep(max(0, self.last.get(origin,0)+self.delay-time.monotonic()))
            headers = dict(request_headers or {})
            status, response_headers, body = self._open_pinned_response(url, addresses, headers)
            record.update(status=status, content_type=response_headers.get('content-type',''),
                          etag=response_headers.get('etag'), last_modified=response_headers.get('last-modified'),
                          cf_mitigated=response_headers.get('cf-mitigated'),
                          retry_after=response_headers.get('retry-after'),
                          location=response_headers.get('location'), headers=response_headers,
                          bytes=len(body),
                          state='not_modified' if status==304 else
                                'body_too_large' if len(body)>3_000_000 else
                                'ok' if status==200 else 'http_error')
            if self.origin_pacer is not None and status in {429, 503}:
                self.origin_pacer.defer(origin, record.get('retry_after'))
            if self.origin_pacer is None:
                self.last[origin] = time.monotonic()
        except (OSError, ValueError, http.client.HTTPException) as error:
            record.update(state='network_error', error=str(error))
        record.update(elapsed_seconds=round(time.monotonic()-start,3), sha256=hashlib.sha256(body).hexdigest())
        key = hashlib.sha256(url.encode()).hexdigest()[:20]
        record['capture'] = key
        (self.out/(key+'.body')).write_bytes(body)
        (self.out/(key+'.json')).write_text(json.dumps(record,indent=2)+'\n')
        self.records.append(record)
        self.used.add(url)
        self.cache[url] = (record,body)
        return record,body

    def get(self, url):
        chain=[]
        for _ in range(6):
            record,body=self._raw(url)
            chain.append(url)
            if is_bot_challenge(body, record.get('cf_mitigated')):
                return {**record, 'state': 'bot_challenge',
                        'error': 'The source returned a bot or CAPTCHA challenge.',
                        'final_url': url, 'redirect_chain': chain}, body
            if record.get('status') in {301,302,303,307,308} and record.get('location'):
                url=urljoin(url,record['location'])
                continue
            return {**record,'final_url':url,'redirect_chain':chain,
                    'access_mode':'public_http'},body
        return {'url':url,'state':'redirect_limit','redirect_chain':chain},b''

    def get_feed(self, url, conditional_headers=None):
        """Only documented, public, read-only ATS endpoints (not website indexing)."""
        p = urlsplit(url)
        host = p.hostname or ''
        token = r'[A-Za-z0-9][A-Za-z0-9_.-]*'
        from .ats import _recruitee_host
        allowed = (
            host == 'boards-api.greenhouse.io' and re.fullmatch(r'/v1/boards/'+token+r'/jobs',p.path) or
            host in {'api.lever.co','api.eu.lever.co'} and re.fullmatch(r'/v0/postings/'+token,p.path) or
            host == 'api.ashbyhq.com' and re.fullmatch(r'/posting-api/job-board/'+token,p.path) or
            re.fullmatch(r'[\w-]+\.jobs\.personio\.(de|com)',host) and p.path == '/xml' or
            _recruitee_host(host) and p.path == '/api/feeds/offers.xml' and not p.query and not p.fragment
        )
        if p.scheme != 'https' or p.username or p.password or p.port not in (None,443) or not allowed:
            return {'url':url,'state':'unsupported_api'},b''
        record, body = self._raw(url, conditional_headers)
        if is_bot_challenge(body, record.get('cf_mitigated')):
            return {**record, 'state': 'bot_challenge',
                    'error': 'The source returned a bot or CAPTCHA challenge.',
                    'final_url': url}, body
        return {**record,'final_url':url,'access_mode':'documented_public_feed'}, body
