"""Optional laptop GPU for the local models, switched on and off in Settings.

A laptop in the same VPN runs the same pinned llama.cpp models (set up with the script
from ``GET /api/v1/matching-model/laptop-setup.sh``) on ports 8091 and 8092. While it is
enabled and answers with the pinned model, the worker sends local model calls there;
when the laptop sleeps or leaves the VPN, calls go back to the homelab ``llm`` and
``llm-reranker`` containers. Both run the same weights under the same model keys, so
stored results stay valid whichever machine produced them.

Each laptop server reports its parallel slots; the worker sends that many requests at
once so the GPU stays busy. The homelab containers keep one request at a time.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from hiring_scraper.app import codex_runtime
from hiring_scraper.job_analysis import LLM_MODEL_KEY
from hiring_scraper.matching_v3.evidence import RERANK_MODEL_KEY

LLM_PORT = 8091
RERANK_PORT = 8092
PROBE_TIMEOUT_SECONDS = 2.0
PROBE_CACHE_SECONDS = 20.0
MAX_PARALLEL = max(1, int(os.getenv('JOB_ANALYSIS_MAX_PARALLEL', '4')))
_HOSTNAME = re.compile(r'^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
                       r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')
_KINDS = {'llm': (LLM_PORT, LLM_MODEL_KEY, 'JOB_ANALYSIS_LLM_URL', 'http://127.0.0.1:8091'),
          'reranker': (RERANK_PORT, RERANK_MODEL_KEY, 'JOB_ANALYSIS_RERANK_URL', 'http://127.0.0.1:8092')}
_probes: dict[str, tuple[float, int | None]] = {}
_lock = threading.Lock()


def _settings_path() -> Path:
    return codex_runtime._root() / 'laptop-gpu.json'


def normalise_address(value: str) -> str:
    """A VPN IP address or host name, without scheme or port."""
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value.strip('[]')))
    except ValueError:
        pass
    if not _HOSTNAME.match(value):
        raise ValueError('Enter the laptop\'s VPN IP address or host name, without http:// or a port.')
    return value.lower()


def settings() -> dict:
    try:
        data = json.loads(_settings_path().read_text())
        address = normalise_address(str(data.get('address') or '')) if data.get('address') else ''
        return {'enabled': bool(data.get('enabled')) and bool(address), 'address': address}
    except (OSError, ValueError, AttributeError):
        return {'enabled': False, 'address': ''}


def save(enabled: bool, address: str) -> dict:
    address = normalise_address(address) if address.strip() else ''
    if enabled and not address:
        raise ValueError('Enter the laptop\'s VPN address first.')
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'enabled': enabled, 'address': address}))
    os.replace(temporary, path)
    with _lock:
        _probes.clear()
    return settings()


def laptop_url(address: str, kind: str) -> str:
    host = f'[{address}]' if ':' in address else address
    return f'http://{host}:{_KINDS[kind][0]}'


def server_url(kind: str) -> str:
    _, _, variable, default = _KINDS[kind]
    return os.getenv(variable, default).rstrip('/')


def _probe(url: str, model_key: str) -> int | None:
    """Parallel slots of a healthy server running the pinned model, else ``None``."""
    try:
        with urlopen(f'{url}/health', timeout=PROBE_TIMEOUT_SECONDS) as response:
            response.read(4096)
        with urlopen(f'{url}/props', timeout=PROBE_TIMEOUT_SECONDS) as response:
            props = json.loads(response.read(1_000_000))
    except (OSError, URLError, ValueError):
        return None
    if props.get('model_alias') != model_key:
        return None  # Another model answers on this port; never mix its results in.
    try:
        return max(1, int(props.get('total_slots') or 1))
    except (TypeError, ValueError):
        return 1


def _cached_probe(url: str, model_key: str, *, fresh: bool = False) -> int | None:
    now = time.monotonic()
    with _lock:
        cached = _probes.get(url)
    if cached is not None and not fresh and now - cached[0] < PROBE_CACHE_SECONDS:
        return cached[1]
    slots = _probe(url, model_key)
    with _lock:
        _probes[url] = (time.monotonic(), slots)
    return slots


def endpoint(kind: str) -> tuple[str, int]:
    """Base URL for this model and how many requests to send at once."""
    current = settings()
    if current['enabled']:
        url = laptop_url(current['address'], kind)
        slots = _cached_probe(url, _KINDS[kind][1])
        if slots is not None:
            return url, min(slots, MAX_PARALLEL)
    return server_url(kind), 1


def serving() -> bool:
    """True while at least one model runs on the laptop."""
    return settings()['enabled'] and any(endpoint(kind)[0] != server_url(kind) for kind in _KINDS)


def mark_unreachable(url: str) -> None:
    """A failed call: route around the laptop until the next probe finds it again."""
    if url not in {server_url(kind) for kind in _KINDS}:
        with _lock:
            _probes[url] = (time.monotonic(), None)


def status() -> dict:
    """Settings plus a fresh check of both laptop servers (probed in parallel)."""
    current = settings()
    result: dict = {**current, 'llm': None, 'reranker': None}
    if current['address']:
        with ThreadPoolExecutor(len(_KINDS)) as pool:
            probes = {kind: pool.submit(_cached_probe, laptop_url(current['address'], kind), _KINDS[kind][1],
                                        fresh=True) for kind in _KINDS}
        for kind, future in probes.items():
            slots = future.result()
            result[kind] = {'online': slots is not None, 'slots': slots or 0}
    return result
