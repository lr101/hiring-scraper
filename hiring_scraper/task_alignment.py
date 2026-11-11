"""Bounded, source-backed action/deliverable alignment without job-title scoring.

This deterministic channel proposes task evidence. It does not certify formal
qualifications, professional duration, regulatory scope or hiring probability.
"""
from __future__ import annotations

import hashlib
import re
from itertools import islice

from hiring_scraper.requirements import scoped_sentences, structured_requirement_texts, structured_text

VERSION = 'task-alignment-v4-atomic-profile-actions'
_ACTIONS = {
    'maintain': r'maintain(?:s|ed|ing)?|pfleg\w*|wart\w*',
    'coordinate': r'coordinat(?:e|es|ed|ing|ion)|koordinier\w*|organis\w*',
    'create': r'design(?:s|ed|ing)?|develop(?:s|ed|ing)?|creat(?:e|es|ed|ing)|gestalt\w*|entwickel\w*',
    'analyse': r'analy[sz](?:e|es|ed|ing)|analysier\w*|untersuch\w*',
    'document': r'document(?:s|ed|ing)?|dokumentier\w*|beschreib\w*',
    'improve': r'improv(?:e|es|ed|ing)|optimier\w*|verbesser\w*',
    'deliver': r'deliver(?:s|ed|ing)?|durchführ\w*|umsetz\w*',
    'support': r'assist(?:s|ed|ing)?|support(?:s|ed|ing)?|unterstütz\w*|betreu\w*',
    'own': r'own(?:s|ed|ing)?|approv(?:e|es|ed|ing)|verantwort(?:en|et|est)',
    'interview': r'interview(?:s|ed|ing)?|befrag\w*',
    'build': r'build(?:s|ing)?|built|erstell\w*|bau\w*',
    'review': r'review(?:s|ed|ing)?|evaluat(?:e|es|ed|ing)|bewert\w*|prüf\w*',
}
_PATTERNS = {name: re.compile(r'\b(?:' + pattern + r')\b', re.I) for name, pattern in _ACTIONS.items()}
_COMPOUND_DUTY = re.compile(
    r'\s+\b(?:and|und)\s+(?=(?:' + '|'.join(_ACTIONS.values()) + r')\b)', re.I)
_STOP = set('a an the and or of to for with in on at by from as is are was were be been this that these those '
            'your our their you we they it its into through across using used use work works team teams '
            'project projects process processes responsibility responsibilities task tasks role core '
            'die der das den dem des ein eine einen einer einem und oder von für mit im in an auf zu '
            'sie ihr ihre ihren ihrer ihrem wir unser unsere sowie bei durch als aus werden wird ist sind '
            'arbeiten arbeit aufgaben verantwortlichkeiten'.split())
_STOP.update('experience need needs knowledge skill skills professional academic required necessary expertise erfahrung kenntnisse erforderlich'.split())
_NEGATIVE = re.compile(r'\b(?:never|not|no|cannot|without|lack\w*|kein\w*|nicht|ohne|wish\w*|aspir\w*|want\w*|möcht\w*)\b', re.I)
_OTHER_ACTOR = re.compile(r'^(?:(?:our|the|a|another|other)\s+)?(?:finance|people|HR|recruiting|legal|design|innovation)\s+(?:team|department)\b|'
                          r'^(?:senior colleagues|the director|die personalabteilung|unsere personalabteilung)\b', re.I)


def _actions(text):
    result = {name for name, pattern in _PATTERNS.items() if pattern.search(text)}
    if re.search(r'(?:^|[,;]|\b(?:you|we|will|and|und)\s+)\s*(?:lead(?:s|ing)?|led)\b', text, re.I):
        result.add('own')
    return result


def _objects(text):
    text = text.casefold()
    for pattern in _PATTERNS.values():
        text = pattern.sub(' ', text)
    words = set(re.findall(r'[^\W\d_][\w-]*', text, re.U)) - _STOP
    return {word[:-1] if len(word) > 4 and word.endswith('s') else word for word in words if len(word) > 2}


def _sentences(text):
    return scoped_sentences(structured_text(text))


def other_actor(text):
    return bool(_OTHER_ACTOR.search(text))


def task_units(job):
    texts = [('description', job.get('description') or '', False)]
    raw = job.get('raw_metadata') or {}
    for prefix, data in [('structured', raw), ('detail', raw.get('detail_requirements') or {})]:
        for field in ('responsibilities', 'duties'):
            for source, text in structured_requirement_texts({field: data.get(field)}, prefix):
                texts.append((source, text, True))
    rows, seen = [], set()
    for source, text, explicit in texts:
        for sentence, scope in _atomic_duties(text):
            if scope in {'requirements', 'optional', 'benefits'} and not explicit:
                continue
            if _OTHER_ACTOR.search(sentence) or _NEGATIVE.search(sentence):
                continue
            sentence = sentence[:1000]
            actions, objects = _actions(sentence), _objects(sentence)
            key = ' '.join(sentence.casefold().split())
            if not actions or not objects or key in seen:
                continue
            seen.add(key)
            rows.append({'id': hashlib.sha256(key.encode()).hexdigest()[:12], 'job_source': source,
                         'job_quote': sentence[:1000], 'actions': sorted(actions), 'objects': sorted(objects)})
            if len(rows) >= 40:
                return rows
    return rows


def _atomic_duties(text):
    """Split coordinated predicates, retaining literal source excerpts.

    A noun list stays together: only a conjunction followed by a recognized
    action begins another duty. Negated or other-actor sentences remain whole
    so their scope cannot be lost by splitting.
    """
    for sentence, scope in _sentences(text):
        if _OTHER_ACTOR.search(sentence) or _NEGATIVE.search(sentence):
            yield sentence, scope
        else:
            for part in _COMPOUND_DUTY.split(sentence):
                if part.strip():
                    yield part.strip(), scope


def align_tasks(job, profile_sources):
    facts = []
    for source, text in islice(profile_sources, 80):
        if not isinstance(text, str):
            continue
        for sentence, _ in _atomic_duties(text[:12000]):
            if _OTHER_ACTOR.search(sentence) or _NEGATIVE.search(sentence):
                continue
            sentence = sentence[:1000]
            actions, objects = _actions(sentence), _objects(sentence)
            if actions and objects:
                facts.append((source, sentence[:1000], actions, objects))
            if len(facts) >= 240:
                break
        if len(facts) >= 240:
            break
    rows = []
    for task in task_units(job):
        best, best_rank = None, (0, 0.)
        actions, objects = set(task['actions']), set(task['objects'])
        for source, quote, fact_actions, fact_objects in facts:
            shared = objects & fact_objects
            coverage = len(shared) / max(1, len(objects))
            common_action = bool(actions & fact_actions)
            ownership_missing = 'own' in actions and 'own' not in fact_actions
            direct = common_action and len(shared) >= min(2, len(objects)) and coverage >= .5 and not ownership_missing
            adjacent = bool(shared) and (common_action or len(shared) >= 2)
            strength = 1 if direct else .5 if adjacent else 0
            if (strength, coverage) > best_rank:
                best_rank = (strength, coverage)
                best = (source, quote, sorted(shared))
        status = 'supported' if best_rank[0] == 1 else 'transferable' if best_rank[0] else 'unknown'
        source, quote, shared = best or ('', '', [])
        rows.append({**task, 'status': status, 'evidence_source': source, 'evidence_quote': quote,
                     'evidence_context': 'academic' if source.startswith('cv_career_facts.education[') else
                                         'professional' if source.startswith(('experience.', 'cv_career_facts.experiences[')) else 'unspecified',
                     'shared_objects': shared,
                     'explanation': {'supported': 'Same documented action and deliverable terms; verify any separate authority, domain or tenure prerequisites.',
                                     'transferable': 'Adjacent action or deliverable evidence; complete task scope is not established.',
                                     'unknown': 'No source-backed action and deliverable alignment found; absence is not proof of inability.'}[status]})
    score = round(100 * sum({'supported': 1, 'transferable': .5, 'unknown': 0}[row['status']] for row in rows) / len(rows)) if rows else None
    return {'version': VERSION, 'score': score, 'requirements': rows, 'facts_considered': len(facts),
            'qualification_proof': False}
