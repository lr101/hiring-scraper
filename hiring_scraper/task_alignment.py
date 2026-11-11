"""Bounded, source-backed action/deliverable alignment without job-title scoring.

This deterministic channel proposes task evidence. It does not certify formal
qualifications, professional duration, regulatory scope or hiring probability.
"""
from __future__ import annotations

import hashlib
import re
from itertools import islice

from hiring_scraper.requirements import (
    is_source_instruction, requirement_polarity, scoped_sentences,
    structured_requirement_texts, structured_text,
)

VERSION = 'task-alignment-v8-responsibility-context'
_ACTIONS = {
    'maintain': r'maintain(?:s|ed|ing)?|maintenance|pfleg\w*|wart\w*',
    'coordinate': r'coordinat(?:e|es|ed|ing|ion)|koordinier\w*|organis\w*',
    'create': r'design(?:s|ed|ing)?|develop(?:s|ed|ing)?|creat(?:e|es|ed|ing|ion)|gestalt\w*|entwickel\w*',
    'analyse': r'analy[sz](?:e|es|ed|ing)|analysier\w*|untersuch\w*',
    'document': r'document(?:s|ed|ing|ation)?|dokumentier\w*|dokumentation|beschreib\w*',
    'improve': r'improv(?:e|es|ed|ing)|optimier\w*|verbesser\w*',
    'deliver': r'deliver(?:s|ed|ing)?|durchführ\w*|umsetz\w*',
    'support': r'assist(?:s|ed|ing)?|support(?:s|ed|ing)?|unterstütz\w*|betreu\w*',
    'own': r'own(?:s|ed|ing)?|approv(?:e|es|ed|ing)|verantwort(?:en|et|est)',
    'interview': r'interview(?:s|ed|ing)?|befrag\w*',
    'build': r'build(?:s|ing)?|built|erstell\w*|bau\w*',
    'review': r'review(?:s|ed|ing)?|evaluat(?:e|es|ed|ing)|bewert\w*|prüf(?:e|en|t|te|ten|end|ung)?',
    'plan': r'plan(?:s|ned|ning)?|planen|plant|plante\w*|Planung',
    'prepare': r'prepar(?:e|es|ed|ing|ation)|vorbereit\w*',
    'track': r'track(?:s|ed|ing)?|nachverfolg\w*',
    'schedule': r'schedul(?:e|ed|ing)|terminier\w*',
    'research': r'research(?:es|ed|ing)?|recherch\w*',
    'record': r'record(?:s|ed|ing)?|erfass\w*|protokollier\w*',
    'collect': r'collect(?:s|ed|ing|ion)?|sammel\w*|samml\w*',
    'draw': r'draw(?:s|ing|n)?|drew|zeichn\w*',
    'compare': r'compar(?:e|es|ed|ing|ison)|vergleich\w*|verglich\w*',
    'summarize': r'summari[sz](?:e|es|ed|ing)|zusammenfass\w*|fass\w*',
    'present': r'present(?:s|ed|ing)?|präsentier\w*',
}
_PATTERNS = {name: re.compile(r'\b(?:' + pattern + r')\b', re.I) for name, pattern in _ACTIONS.items()}
_STOP = set('a an the and or of to for with in on at by from as is are was were be been this that these those '
            'your our their you we they it its into through across using used use work works team teams '
            'project projects process processes responsibility responsibilities task tasks role core '
            'die der das den dem des ein eine einen einer einem und oder von für mit im in an auf zu '
            'sie ihr ihre ihren ihrer ihrem wir unser unsere sowie bei durch als aus werden wird ist sind '
            'arbeiten arbeit aufgaben verantwortlichkeiten'.split())
_STOP.update('experience need needs knowledge skill skills professional academic required necessary expertise erfahrung kenntnisse erforderlich'.split())
_STOP.update('evaluation support excel powerpoint word microsoft office tools tool software '
             'independently independent supervision supervised under junior senior '
             'eigenständig selbstständig selbständig unter anleitung aufsicht '
             'pflege erstellung durchführung unterstützung koordination'.split())
_NEGATIVE = re.compile(r'\b(?:never|not|no|cannot|without|lack\w*|kein\w*|nicht|ohne|wish\w*|aspir\w*|want\w*|möcht\w*)\b', re.I)
_OTHER_ACTOR = re.compile(r'^(?:(?:our|the|a|another|other)\s+)?(?:finance|people|HR|recruiting|legal|design|innovation)\s+(?:team|department)\b|'
                          r'^(?:our|the|a|an|another|other|my|their|ein\w*|meine?\w*|unsere?\w*|die|das|der)\s+(?:(?:[\w-]+)\s+){0,2}'
                          r'(?:team|department|manager|colleague\w*|kolleg\w*|abteilung)\b|'
                          r'^(?:(?:(?:a|the|our|ein\w*|der|die)\s+)?senior\s+(?:colleague\w*|manager\w*|kolleg\w*)|the director|die personalabteilung|unsere personalabteilung)\b|'
                          r'^(?:(?:this|the|our|diese\w*)\s+)?(?:vacancy|posting|advertisement|job ad|job description|stellenanzeige|stellenbeschreibung|ausschreibung)\b', re.I)
# A local explicit subject switch ends the applicant's predicate scope.
_APPLICANT_ACTOR = re.compile(r'^(?:I|we|you|ich|wir)\s+', re.I)
_GOAL = re.compile(r'\b(?:goal|aim|ambition|objective|ziel)\s+(?:is|was|ist|war)\b|\b(?:hope|plan|intend|aim)\s+to\b', re.I)
_BOUNDED = re.compile(r'\b(?:under supervision|supervised|assist(?:s|ed|ing)?|support(?:s|ed|ing)?|unterstütz\w*|unter Anleitung|unter Aufsicht|student\w*|university|universit\w*|academic|akademisch\w*)\b', re.I)
_INDEPENDENT = re.compile(r'\b(?:independent\w*|eigenständig\w*|selbst[äa]ndig\w*|senior|strategic\w*)\b', re.I)
_INTEREST = re.compile(r'\b(?:interested\s+in|interests?(?=\s*:)|research interests?|'
                       r'Interesse\s+an|Forschungsinteressen)\b', re.I)
_DOCUMENT_CONTENT = re.compile(
    r'^(?:(?:the|a|an|this|our|my|der|die|das|ein\w*)\s+)?(?:[\w-]+\s+){0,2}'
    r'(?:plan|documents?|reports?|articles?|papers?|notes?|topics?|interests?|Dokument|Bericht)\s*'
    r'(?::|(?:contains?|describes?|covers?|includes?|outlines?|lists?|details?|enthält|beschreibt)\b)', re.I)

_COMPOUND_DUTY = re.compile(
    r'\s+\b(?:and|und)\s+(?=(?:' + '|'.join(_ACTIONS.values()) +
    r')\b|(?:' + _OTHER_ACTOR.pattern.replace('^', '') + r')|(?:' +
    _APPLICANT_ACTOR.pattern.removeprefix('^') + r')|(?:' +
    _DOCUMENT_CONTENT.pattern.removeprefix('^') + r')|(?:' + _INTEREST.pattern + r'))', re.I)

# Work-product concepts, not titles or broad domain words. Canonicalization is
# internal only; citations and shared literal words always retain their source.
_DELIVERABLES = {
    'project_charter': r'project charters?|Projektauftrag\w*',
    'work_breakdown': r'work breakdown structures?|WBS|Projektstrukturpl[äa]n\w*',
    'milestone': r'milestones?|Meilenstein\w*',
    'schedule': r'schedules?|timelines?|Terminpl[äa]n\w*|Zeitpl[äa]n\w*',
    'action_register': r'action (?:registers?|logs?|trackers?)|Ma[ßs]nahmenlist\w*|Maßnahmenverfolgung',
    'issue_register': r'issue (?:registers?|logs?|trackers?)|Problemlist\w*',
    'raci': r'RACI(?: matrices| matrix|-Matriz\w*)?',
    'procedure': r'SOPs?|standard operating procedures?|work instructions?|Arbeitsanweisung\w*|Verfahrensanweisung\w*',
    'presentation': r'slide decks?|(?:progress )?slides?|presentations?|Fortschrittsfoli\w*|Präsentation\w*',
    'as_is_process_map': r'as-is process maps?|Ist-Prozesslandkart\w*',
    'to_be_process_map': r'to-be process maps?|Soll-Prozesslandkart\w*',
    'process_map': r'process maps?|process mapping|process flows?|Prozesslandkart\w*|Prozessabl[äa]uf\w*|Ablaufdiagramm\w*',
    'workflow': r'workflows?|Arbeitsabl[äa]uf\w*',
    'project_team': r'project teams?|Projektteam\w*',
    'training_session': r'training sessions?|workshops?|Schulung\w*',
}
_DELIVERABLE_PATTERNS = {name: re.compile(r'\b(?:' + pattern + r')\b', re.I)
                         for name, pattern in _DELIVERABLES.items()}
_ADJACENT_ACTIONS = {frozenset(pair) for pair in (
    ('maintain', 'create'), ('maintain', 'document'), ('maintain', 'prepare'),
    ('maintain', 'plan'), ('maintain', 'schedule'), ('maintain', 'track'),
    ('maintain', 'review'), ('create', 'prepare'), ('plan', 'schedule'),
    ('coordinate', 'plan'), ('coordinate', 'schedule'), ('analyse', 'research'),
)}


def _actions(text):
    result = {name for name, pattern in _PATTERNS.items() if pattern.search(text)}
    # "Inspection records" is an object; "record observations" is a duty.
    if 'record' in result and not re.search(
            r'\b(?:recorded|recording|erfass\w*|protokollier\w*)\b|'
            r'^(?:(?:I|we|you)\s+)?(?:(?:will|can|must|should)\s+)?(?:\w+ly\s+){0,2}records?\s+(?!:)', text, re.I):
        result.remove('record')
    if re.search(r'(?:^|[,;]|\b(?:you|we|will|and|und)\s+)\s*(?:lead(?:s|ing)?|led)\b', text, re.I):
        result.add('own')
    for name, pattern in (('coordinate', r'Koordination'), ('deliver', r'Durchführung'),
                          ('support', r'Unterstützung')):
        if re.search(r'\b' + pattern + r'\b', text, re.I):
            result.add(name)
    if 'build' in result:
        result.remove('build')
        result.add('create')
    # Drawing a concrete map/diagram creates that work product; it does not
    # confer ownership or independent engineering authority.
    if 'draw' in result and re.search(
            r'\b(?:maps?|diagrams?|Prozesslandkart\w*|Ablaufdiagramm\w*)\b', text, re.I):
        result.add('create')
    # The noun in "maintain the schedule" is not a second predicate.
    if 'schedule' in result and not re.search(
            r'\b(?:scheduled|scheduling|terminier\w*)\b|(?:^|:\s*|\b(?:we|I|you|will)\s+)schedule\b', text, re.I):
        result.remove('schedule')
    return result


def _objects(text):
    text = text.casefold()
    for name, pattern in _PATTERNS.items():
        if name == 'record':
            # Retain object nouns when removing action vocabulary.
            text = re.sub(r'\b(?:recorded|recording|erfass\w*|protokollier\w*)\b', ' ', text)
            text = re.sub(r'^(?:(?:i|we|you)\s+)?records?\s+', ' ', text)
        else:
            text = pattern.sub(' ', text)
    words = set(re.findall(r'[^\W\d_][\w-]*', text, re.U)) - _STOP
    return {word[:-1] if len(word) > 4 and word.endswith('s') else word for word in words if len(word) > 2}


def _profile_actions(text):
    """Profile facts need predicates, not ambiguous plan/research nouns.

    Jobs retain nominal duty extraction; profiles accept performed/ongoing
    forms at predicate position or a personal task predicate for these
    ambiguous families. A topic noun inside a document subject is not a verb.
    """
    # Nominal duty labels can describe a job, but do not assert performed work
    # in an applicant inventory. Keep genuine verb forms in the same sentence.
    predicate_text = re.sub(
        r'\b(?:maintenance|creation|coordination|documentation|preparation|collection|comparison|'
        r'Pflege|Erstellung|Koordination|Dokumentation|Vorbereitung|Sammlung|Vergleich|Erfassung\w*|Zeichnung\w*|Zusammenfassung\w*)\b',
        ' ', text, flags=re.I)
    actions = _actions(predicate_text)
    for action, performed in (
            ('plan', r'planned|planning|planen|plante\w*'),
            ('research', r'researched|researching|recherchier\w*')):
        intro = r'^(?:[^.!?;:,]+[:,]\s*)?'
        personal = (r'(?:I|we|you|ich|wir)\s+(?:(?:am|was|were|have|had|been|bin|habe)\s+)*'
                    r'(?:(?:[a-z]+ly|eigenständig|selbstständig|aktiv|regelmäßig)\s+){0,2}')
        predicate = re.search(intro + r'(?:' + personal + r')?(?:' + performed + r')\b', text, re.I)
        predicate = predicate or re.search(
            intro + personal + action + r'\b', text, re.I)
        if not predicate:
            actions.discard(action)
    return actions


def _semantic_objects(text):
    # Imperative "schedule workshops" names an action, not two deliverables.
    text = re.sub(r'^(?:(?:you|we|I)\s+)?schedule\b', ' ', text, flags=re.I)
    # Descriptive detail does not change a deliverable's domain or autonomy.
    text = re.sub(r'\b(?:detailed|detailliert\w*)\b', ' ', text, flags=re.I)
    concepts = set()
    for name, pattern in _DELIVERABLE_PATTERNS.items():
        if pattern.search(text):
            concepts.add('concept:' + name)
            text = pattern.sub(' ', text)
    return _objects(text) | concepts


# These describe posting administration or attendance, rather than an actor's
# work product. Active recruiting and scheduling duties remain available.
_NON_DUTY_CONTEXT = re.compile(
    r'^(?:\w+\s+){0,3}(?:job posting|job advertisement|vacancy|Stellenausschreibung)\s+'
    r'(?:(?:was|is|were|wurde|wird|werden)\s+)?(?:created|composed|erstellt)\b|'
    r'^(?:English|German|French|Spanish|Deutsch|Englisch|Französisch|Spanisch)\s+[ABC][12]\b|'
    r'^(?:applications?|Bewerbungen)\s+(?:are|will be|werden)\s+'
    r'(?:reviewed|evaluated|geprüft|bewertet)\b.*\b(?:recruitment|recruiting|Personalabteilung)\b|'
    r'^(?:we|wir)\s+(?:review|evaluate|prüfen|bewerten)\s+(?:your application|Ihre Bewerbung)\b|'
    r'\b(?:you are|you will be|Sie sind|Sie werden)\s+(?:scheduled|eingeplant)\s+(?:to work|to attend|im Büro)\b|'
    r'^(?:(?:the|die)\s+)?(?:documentation|Dokumentation)\s+'
    r'(?:is|will be|erfolgt|wird)\s+(?:(?:maintained|written|prepared|geführt|erstellt)\s+)?in\s+'
    r'(?:fluent\w*|fließend\w*|English|German|French|Spanish|Portuguese|Danish|Dutch|Italian|'
    r'Deutsch|Englisch|Französisch|Spanisch|Portugiesisch|Dänisch|Niederländisch|Italienisch)\b', re.I)


def _excluded(text):
    return (other_actor(text) or bool(_NON_DUTY_CONTEXT.search(text))
            or bool(_NEGATIVE.search(text)) or bool(_GOAL.search(text))
            or requirement_polarity(text, 0, len(text)) in {'instruction', 'waived'})


def _academic(source, quote):
    return source.startswith('cv_career_facts.education[') or bool(re.search(
        r'\b(?:student\w*|university|universit\w*|academic|akademisch\w*)\b', quote, re.I))


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
        for full_sentence, scope in _sentences(text):
            governing_clause = _COMPOUND_DUTY.split(full_sentence)[0]
            for sentence, _ in _atomic_duties(full_sentence):
                if scope in {'requirements', 'optional', 'benefits'} and not explicit:
                    continue
                if _excluded(sentence):
                    continue
                sentence = sentence[:1000]
                actions, objects = _actions(sentence), _objects(sentence)
                key = ' '.join(sentence.casefold().split())
                if not actions or not _semantic_objects(sentence) or key in seen:
                    continue
                seen.add(key)
                rows.append({'id': hashlib.sha256(key.encode()).hexdigest()[:12], 'job_source': source,
                             'job_quote': sentence[:1000],
                             'independent_scope': bool(_INDEPENDENT.search(governing_clause)
                                                       or _INDEPENDENT.search(sentence)),
                             'actions': sorted(actions), 'objects': sorted(objects)})
                if len(rows) >= 40:
                    return rows
    return rows


def _document_subject(text):
    if not _DOCUMENT_CONTENT.search(text):
        return False
    personal = _APPLICANT_ACTOR.match(text)
    if personal:
        predicate = re.match(r'\s*(\w+)', text[personal.end():])
        if predicate and _actions(predicate[1]):
            return False
    first = re.match(r'\w+', text)
    if first:
        word = first.group().casefold()
        if _actions(word) and (word.endswith(('ed', 'te', 'ten')) or word == 'built'):
            return False
    return True


def _atomic_duties(text):
    """Split coordinated predicates, retaining literal source excerpts.

    A noun list stays together: only a conjunction followed by a recognized
    action begins another duty. Negated or other-actor sentences remain whole
    so their scope cannot be lost by splitting.
    """
    for sentence, scope in _sentences(text):
        if is_source_instruction(sentence):
            continue
        if _excluded(sentence) and not other_actor(sentence):
            yield sentence, scope
        else:
            applicant_scope = True
            for part in _COMPOUND_DUTY.split(sentence):
                part = part.strip()
                if other_actor(part) or _document_subject(part) or _INTEREST.search(part):
                    applicant_scope = False
                elif _APPLICANT_ACTOR.search(part):
                    applicant_scope = True
                if part and applicant_scope:
                    yield part, scope


def align_tasks(job, profile_sources, *, source_contexts=None):
    """Align literal source pairs, optionally retaining declared source context.

    Context labels bound task scope; they never grant an action or authority
    absent from the actual source. Source paths remain caller-owned strings.
    """
    source_contexts = source_contexts or {}
    facts = []
    for source, text in islice(profile_sources, 80):
        if not isinstance(text, str):
            continue
        for full_sentence, _ in _sentences(text[:12000]):
            # An academic/supervision prefix governs coordinated predicates.
            # Retain it in the literal citation while matching only the atomic
            # action, so neither autonomy nor ownership leaks across clauses.
            full_sentence = full_sentence[:1000]
            bounded = bool(_BOUNDED.search(full_sentence))
            for sentence, _ in _atomic_duties(full_sentence):
                if _excluded(sentence) or _INTEREST.search(sentence):
                    continue
                actions, objects = _profile_actions(sentence), _semantic_objects(sentence)
                if actions and objects:
                    quote = full_sentence if bounded else sentence
                    declared = source_contexts.get(source)
                    context = ('academic' if _academic(source, quote) else
                               declared if declared in {'academic', 'research', 'professional', 'unspecified'} else
                               'professional' if source.startswith(('experience.', 'cv_career_facts.experiences[')) else
                               'unspecified')
                    facts.append((source, quote, actions, objects, context))
                if len(facts) >= 240:
                    break
            if len(facts) >= 240:
                break
        if len(facts) >= 240:
            break
    rows = []
    for task in task_units(job):
        best, best_rank = None, (0, 0.)
        actions, objects = set(task['actions']), _semantic_objects(task['job_quote'])
        for source, quote, fact_actions, fact_objects, context in facts:
            shared = objects & fact_objects
            coverage = len(shared) / max(1, len(objects))
            common_action = bool(actions & fact_actions)
            ownership_missing = 'own' in actions and 'own' not in fact_actions
            bounded_scope = (context in {'academic', 'research'} or bool(_BOUNDED.search(quote))
                             or 'support' in fact_actions)
            ownership_missing = ownership_missing or (bounded_scope and (
                'own' in actions or task['independent_scope']))
            direct = common_action and len(shared) >= min(2, len(objects)) and coverage >= .5 and not ownership_missing
            # A specific shared work product can establish adjacent lifecycle
            # exposure while retaining different main actions.
            adjacent_action = any(frozenset((action, fact_action)) in _ADJACENT_ACTIONS
                                  for action in actions for fact_action in fact_actions)
            adjacent = bool(shared) and ((common_action and objects == fact_objects) or len(shared) >= 2 or (
                any(obj.startswith('concept:') for obj in shared) and (common_action or adjacent_action)))
            strength = 1 if direct else .5 if adjacent else 0
            if (strength, coverage) > best_rank:
                best_rank = (strength, coverage)
                best = (source, quote, sorted(set(task['objects']) & _objects(quote)), context)
        status = 'supported' if best_rank[0] == 1 else 'transferable' if best_rank[0] else 'unknown'
        source, quote, shared, context = best or ('', '', [], 'unspecified')
        rows.append({**task, 'status': status, 'evidence_source': source, 'evidence_quote': quote,
                     'evidence_context': context,
                     'shared_objects': shared,
                      'explanation': {'supported': 'Same documented action and equivalent deliverable; verify any separate authority, domain or tenure prerequisites.',
                                     'transferable': 'Adjacent action or deliverable evidence; complete task scope is not established.',
                                     'unknown': 'No source-backed action and deliverable alignment found; absence is not proof of inability.'}[status]})
    score = round(100 * sum({'supported': 1, 'transferable': .5, 'unknown': 0}[row['status']] for row in rows) / len(rows)) if rows else None
    return {'version': VERSION, 'score': score, 'requirements': rows, 'facts_considered': len(facts),
            'qualification_proof': False}
