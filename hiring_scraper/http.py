"""Bounded HTTP capture with per-origin pacing and per-hop robots checks."""
import hashlib
import ipaddress
import json
import re
import http.client
import socket
import time
from threading import Lock
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

UA = 'HiringDiscoveryResearch/0.2 (career discovery feasibility study)'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OriginPacer:
    """Pace requests per origin without making unrelated origins wait on each other."""
    def __init__(self):
        self.last: dict[tuple[str, str], float] = {}
        self._locks_guard = Lock()
        self._origin_locks: dict[tuple[str, str], Lock] = {}

    def wait(self, origin: tuple[str, str], delay: float) -> None:
        with self._locks_guard:
            origin_lock = self._origin_locks.setdefault(origin, Lock())
        with origin_lock:
            now = time.monotonic()
            wait = delay - (now - self.last.get(origin, 0.0))
            if wait > 0:
                time.sleep(wait)
            self.last[origin] = time.monotonic()


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
        self.cache, self.robots, self.last, self.records = {}, {}, {}, []
        self.opener = urllib.request.build_opener(NoRedirect)
        self.used = set()
        if cache_from:
            for path in Path(cache_from).glob('*.json'):
                record = json.loads(path.read_text())
                body = path.with_suffix('.body').read_bytes()
                if hashlib.sha256(body).hexdigest() != record['sha256']:
                    raise ValueError('Corrupt cache capture: '+str(path))
                self.cache[record['url']] = ({**record,'reused_from':str(path)},body)

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
            addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme=='https' else 80), type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError('Non-public destination rejected')
            origin = (p.scheme,p.netloc.lower())
            if self.origin_pacer is not None:
                self.origin_pacer.wait(origin,self.delay)
            else:
                time.sleep(max(0, self.last.get(origin,0)+self.delay-time.monotonic()))
            try:
                headers={'User-Agent':self.user_agent}
                headers.update(request_headers or {})
                response = self.opener.open(urllib.request.Request(url,headers=headers), timeout=self.timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read(3_000_001)
                record.update(status=response.code, content_type=response.headers.get('Content-Type',''),
                              etag=response.headers.get('ETag'), last_modified=response.headers.get('Last-Modified'),
                              retry_after=response.headers.get('Retry-After'),
                              location=response.headers.get('Location'), bytes=len(body),
                              state='not_modified' if response.code==304 else
                                    'body_too_large' if len(body)>3_000_000 else
                                    'ok' if response.code==200 else 'http_error')
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

    def _policy(self, url):
        p=urlsplit(url)
        origin=f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            robot_url=origin+'/robots.txt'
            for _ in range(6):
                record,body=self._raw(robot_url)
                if record.get('status') in {301,302,303,307,308} and record.get('location'):
                    robot_url=urljoin(robot_url,record['location'])
                    continue
                break
            if record.get('status')==404:
                self.robots[origin]=None
            elif record.get('state')=='ok' and not body.lstrip().startswith(b'<') and 'html' not in record.get('content_type',''):
                parser=RobotFileParser()
                parser.parse(body.decode('utf-8-sig',errors='replace').splitlines())
                self.robots[origin]=parser
            else:
                self.robots[origin]=False
        policy=self.robots[origin]
        if policy is False: return 'robots_unavailable'
        if policy is not None and not policy.can_fetch(self.user_agent,url): return 'robots_disallowed'
        return None

    def get(self, url):
        chain=[]
        for _ in range(6):
            reason=self._policy(url)
            if reason: return {'url':url,'state':reason,'redirect_chain':chain},b''
            record,body=self._raw(url)
            chain.append(url)
            if record.get('status') in {301,302,303,307,308} and record.get('location'):
                url=urljoin(url,record['location'])
                continue
            return {**record,'final_url':url,'redirect_chain':chain},body
        return {'url':url,'state':'redirect_limit','redirect_chain':chain},b''

    def get_feed(self, url, conditional_headers=None):
        """Only documented, public, read-only ATS endpoints (not website indexing)."""
        p = urlsplit(url)
        host = p.hostname or ''
        token = r'[A-Za-z0-9][A-Za-z0-9_.-]*'
        allowed = (
            host == 'boards-api.greenhouse.io' and re.fullmatch(r'/v1/boards/'+token+r'/jobs',p.path) or
            host in {'api.lever.co','api.eu.lever.co'} and re.fullmatch(r'/v0/postings/'+token,p.path) or
            host == 'api.ashbyhq.com' and re.fullmatch(r'/posting-api/job-board/'+token,p.path) or
            re.fullmatch(r'[\w-]+\.jobs\.personio\.(de|com)',host) and p.path == '/xml'
        )
        if p.scheme != 'https' or p.username or p.password or p.port not in (None,443) or not allowed:
            return {'url':url,'state':'unsupported_api'},b''
        record, body = self._raw(url, conditional_headers)
        return {**record,'final_url':url,'access_mode':'documented_public_feed'}, body
