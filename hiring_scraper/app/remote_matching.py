"""GPT-6 Luna through the ChatGPT subscription as the primary matching model.

The local LLM and reranker stay the backup: Luna is used only while matching is set to
``luna_first``, ChatGPT is connected and the subscription has headroom (the five-hour
window below ``RADIUS_MATCH_LUNA_MAX_5H`` percent and the longest window, usually the
week, below ``RADIUS_MATCH_LUNA_MAX_WEEKLY`` percent). Application documents keep the
remaining quota. Any failure falls back to the local models for that item.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from hiring_scraper.app import codex_runtime
from hiring_scraper.job_analysis import clean_text, prompt_messages
from hiring_scraper.matching_v3.evidence import _job_context, scored_items

LOG = logging.getLogger(__name__)
MODEL = 'gpt-6-luna'
MODES = ('luna_first', 'local')
DEFAULT_MODE = 'luna_first'
USAGE_CACHE_SECONDS = 300
CALL_TIMEOUT_SECONDS = 240
_usage_lock = threading.Lock()
_usage_cache: dict[str, Any] = {'at': 0.0, 'value': None}


def _limit(name: str, default: int) -> int:
    try:
        return max(0, min(100, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _settings_path() -> Path:
    return codex_runtime._root() / 'matching.json'


def mode() -> str:
    try:
        value = json.loads(_settings_path().read_text()).get('mode')
    except (OSError, ValueError, AttributeError):
        value = None
    return value if value in MODES else DEFAULT_MODE


def set_mode(value: str) -> str:
    if value not in MODES:
        raise ValueError('Unknown matching mode')
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'mode': value}))
    os.replace(temporary, path)
    return value


def _usage() -> dict:
    with _usage_lock:
        if _usage_cache['value'] is not None and time.monotonic() - _usage_cache['at'] < USAGE_CACHE_SECONDS:
            return _usage_cache['value']
    try:
        value = codex_runtime.subscription_usage() if codex_runtime.status().get('enabled') else None
    except Exception:  # Usage is advisory; unknown usage means local matching.
        value = None
    with _usage_lock:
        _usage_cache.update(at=time.monotonic(), value=value or {})
    return value or {}


def headroom(usage: dict | None = None) -> tuple[bool, str]:
    """Whether Luna may run now, and why not."""
    usage = _usage() if usage is None else usage
    if not usage or not usage.get('available') or usage.get('used_percent') is None:
        return False, 'ChatGPT usage is unavailable'
    if usage['used_percent'] >= _limit('RADIUS_MATCH_LUNA_MAX_5H', 60):
        return False, 'five-hour ChatGPT window is busy'
    weekly = usage.get('long_used_percent')
    if weekly is not None and weekly >= _limit('RADIUS_MATCH_LUNA_MAX_WEEKLY', 70):
        return False, 'weekly ChatGPT budget is reserved for applications'
    return True, 'ready'


def luna_allowed() -> bool:
    return mode() == 'luna_first' and headroom()[0]


def status() -> dict:
    allowed, reason = headroom() if mode() == 'luna_first' else (False, 'local models selected')
    return {'mode': mode(), 'active': 'luna' if allowed else 'local', 'reason': reason,
            'max_5h_percent': _limit('RADIUS_MATCH_LUNA_MAX_5H', 60),
            'max_weekly_percent': _limit('RADIUS_MATCH_LUNA_MAX_WEEKLY', 70)}


def _generate(prompt: str, schema: dict) -> dict:
    return codex_runtime.generate(prompt, schema, timeout=CALL_TIMEOUT_SECONDS, model=MODEL)


ANALYSIS_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['analysis'],
                   'properties': {'analysis': {'type': 'string'}}}


def analyse_lines(lines: list[str]) -> str:
    """Same prompt and line format as the local LLM; the shared parser grounds the answer."""
    messages = prompt_messages(lines)
    prompt = (messages[0]['content'] + '\n\n' + messages[1]['content']
              + '\n\nReturn the analysis in the exact line format above as the `analysis` string.')
    return str(_generate(prompt, ANALYSIS_SCHEMA).get('analysis') or '')


EVIDENCE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['items', 'role', 'domain'], 'properties': {
    'items': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['ref', 'line', 'support'],
              'properties': {'ref': {'type': 'string'}, 'line': {'type': 'integer'}, 'support': {'type': 'number'}}}},
    'role': {'type': 'object', 'additionalProperties': False, 'required': ['index', 'support'],
             'properties': {'index': {'type': 'integer'}, 'support': {'type': 'number'}}},
    'domain': {'type': 'object', 'additionalProperties': False, 'required': ['line', 'support'],
               'properties': {'line': {'type': 'integer'}, 'support': {'type': 'number'}}}}}


def evidence_prompt(analysis: dict, title: str, source: dict) -> str:
    units, roles = source['units'], source['roles']
    context = _job_context(analysis, clean_text(title))
    field = '; '.join(part for part in (analysis.get('occupation'), analysis.get('industry')) if part)
    return (f'You check a candidate CV against one job. Job: {context}.\n\n'
            'ITEMS (duties the candidate will perform, and requirements the candidate must meet):\n'
            + '\n'.join(f'{item["ref"]} [{item["type"]}] {item["text"]}' for item in scored_items(analysis))
            + '\n\nCV LINES:\n' + '\n'.join(f'{index} {unit["text"]}' for index, unit in enumerate(units))
            + '\n\nCANDIDATE ROLES:\n' + '\n'.join(f'{index} {role}' for index, role in enumerate(roles))
            + '\n\nFor every item, pick the single CV line that best shows the candidate has done this task or meets '
            'this requirement in a comparable field, and give support = probability (0-1) that it really does. '
            'Use 0-0.1 when no line shows it; do not reward keyword overlap without the actual skill. '
            f'role: the candidate role most like the job title "{title}" and the probability it is the same kind of role. '
            f'domain: the work CV line that best shows professional background in "{field or "this field"}" '
            'and its probability. Return JSON only.')


def evidence_from_reply(reply: dict, analysis: dict, title: str, source: dict) -> dict:
    """Map Luna's answer onto the reranker evidence shape (same refs, quotes and sources)."""
    units, roles = source['units'], source['roles']
    clamp = lambda value: max(0.0, min(1.0, float(value)))  # noqa: E731
    by_ref = {row.get('ref'): row for row in reply.get('items') or [] if isinstance(row, dict)}
    result: dict[str, Any] = {'items': [], 'role': None, 'domain': None, 'provider': 'luna',
                              'context': _job_context(analysis, clean_text(title))}
    for item in scored_items(analysis):
        row = by_ref.get(item['ref'])
        line = row.get('line') if row else None
        unit = units[line] if isinstance(line, int) and 0 <= line < len(units) else None
        result['items'].append({'ref': item['ref'], 'support': clamp(row['support']) if row and unit else 0.0,
                                'similarity': None, 'quote': unit['text'][:240] if unit else '',
                                'source': unit['source'] if unit else ''})
    if roles and title:
        index = reply.get('role', {}).get('index')
        role = roles[index] if isinstance(index, int) and 0 <= index < len(roles) else roles[0]
        result['role'] = {'support': clamp(reply.get('role', {}).get('support', 0)), 'role': role}
    field = '; '.join(part for part in (analysis.get('occupation'), analysis.get('industry')) if part)
    if field and units:
        line = reply.get('domain', {}).get('line')
        unit = units[line] if isinstance(line, int) and 0 <= line < len(units) else units[0]
        result['domain'] = {'support': clamp(reply.get('domain', {}).get('support', 0)), 'quote': unit['text'][:240],
                            'source': unit['source'], 'field': field}
    return result


def compute_evidence(analysis: dict, title: str, source: dict) -> dict:
    reply = _generate(evidence_prompt(analysis, title, source), EVIDENCE_SCHEMA)
    return evidence_from_reply(reply, analysis, title, source)
