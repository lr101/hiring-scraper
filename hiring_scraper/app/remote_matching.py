"""GPT-6 Luna through the ChatGPT subscription as a matching model.

Settings → Matching model switches by hand between ``luna`` and ``local``; there is no
automatic fallback, so the local ``llm`` and ``llm-reranker`` containers can stay off
while Luna is selected. When Luna cannot be reached (not signed in, rate limit, outage)
the work stays queued and the worker retries later; only an answer that cannot be
parsed counts against the posting itself.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from hiring_scraper.app import codex_runtime
from hiring_scraper.job_analysis import clean_text, prompt_messages
from hiring_scraper.matching_v3.evidence import _job_context, scored_items

LOG = logging.getLogger(__name__)
MODEL = 'gpt-6-luna'
MODES = ('luna', 'local')
DEFAULT_MODE = 'luna'
_LEGACY_MODES = {'luna_first': 'luna'}
CALL_TIMEOUT_SECONDS = 240


class LunaUnavailable(OSError):
    """Luna cannot be used right now; like a local model outage, the item stays queued."""


def _settings_path() -> Path:
    return codex_runtime._root() / 'matching.json'


def mode() -> str:
    try:
        value = json.loads(_settings_path().read_text()).get('mode')
    except (OSError, ValueError, AttributeError):
        value = None
    value = _LEGACY_MODES.get(value, value)
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


def use_luna() -> bool:
    return mode() == 'luna'


def _connected() -> bool:
    try:
        return bool(codex_runtime.status().get('enabled'))
    except Exception:  # Status is advisory here; a failed call reports the real error.
        return False


def status() -> dict:
    if not use_luna():
        return {'mode': 'local', 'ready': True, 'reason': ''}
    connected = _connected()
    return {'mode': 'luna', 'ready': connected,
            'reason': '' if connected else 'Connect ChatGPT below; matching waits until then.'}


def _generate(prompt: str, schema: dict) -> dict:
    """One Luna call. Service problems raise ``LunaUnavailable``; only a prompt that is too
    long for the model is the posting's own failure."""
    try:
        return codex_runtime.generate(prompt, schema, timeout=CALL_TIMEOUT_SECONDS, model=MODEL)
    except HTTPException as error:
        detail = str(error.detail)
        if 'context limit' in detail:
            raise ValueError(detail) from None
        raise LunaUnavailable(detail) from None


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
