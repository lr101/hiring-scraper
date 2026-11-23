"""Deterministic coverage of documented duties, with source citations."""
from functools import lru_cache
import re

from hiring_scraper.task_alignment import (
    _academic, _atomic_duties, _atomic_job_duties, _excluded, _profile_actions, _semantic_objects,
    _sentences, other_actor,
)

# Work products and activities that have common English and German paraphrases.
# Tools deliberately have no entries here.
_CONCEPTS = {
    'scheduling': r'\b(?:appointments?|schedules?|scheduling|termine?n?|terminplanung|terminkoordination|fristen)\b',
    'meetings': r'\b(?:meetings?|besprechung\w*|veranstaltung\w*|events?|webinars?)\b',
    'administration': r'executive support|administrative support|office administration|büro.{0,8}verwaltungsarbeit|büroalltag|administrative projektbegleitung|support(?:ed|ing) the CEO',
    'decision_support': r'decision.mak\w*|senior leaders?|executive leadership|entscheidungsvorlag\w*',
    'collaboration': r'development teams?|product teams?|entwicklungs?team\w*|projektteam\w*|fachliche leitung',
    'project_reporting': r'projekt(?:dokumentation|fortschritt)|project (?:documentation|progress)',
    'implementation': r'implement\w*|roll.out|einführ\w*|umsetz\w*',
    'user_journeys': r'user (?:flows?|journeys?)|customer journe\w*|nutzerpfad\w*',
    'learning_products': r'learning (?:and development|solutions?|products?)|trainings?lösungen|kursportfolio|weiterbildung|lernformate|lernmaterialien|web.based trainings?|e.learning',
    'coordination': r'coordinate projects|coordinat\w* (?:project|program)|(?:koordinier\w*|steuer\w*).{0,30}projekt',
    'customer_needs': r'(?:customer|user) (?:needs|insight\w*|feedback)|kundenbedürfniss\w*|zielgruppen|teilnehmend\w*',
    'requirements': r'\brequirements?\b|anforderung\w*',
    'specifications': r'\bspecifications?\b|spezifikation\w*',
    'prototypes': r'\bprototyp\w*|wireframes?|mock.ups?',
    'stakeholders': r'\bstakeholder\w*|fachbereich\w*|cross.functional|abteilungsübergreif\w*|schnittstell\w*|kundenbezieh\w*',
    'planning': r'\b(?:deliverables?|milestones?|timelines?|schedules?)\b|meilenstein\w*|terminpl\w*',
    'process_improvement': r'process (?:improvement|optimi[sz]ation)|prozessoptimierung|optimierungspotenzial\w*|prozesse.{0,35}(?:verbessern|optimier)',
    'process_documentation': r'process (?:maps?|documentation)|prozess(?:modell|dokumentation)\w*|\bSOPs?\b|arbeitsanweisung\w*',
    'training': r'\b(?:training|onboarding|workshops?)\b|schulung\w*|einarbeitung\w*',
    'performance_reporting': r'\b(?:KPIs?|dashboards?)\b|kennzahlen|status.report\w*|fortschrittsbericht\w*',
    'service': r'customer (?:service|support|requests?)|kunden(?:service|support|anfragen)|support tickets?',
    'prioritization': r'prioriti[sz]\w*|priorisier\w*|feature priorities',
    'product_development': r'product development|produktentwickl\w*|entwickel\w*.{0,35}produkte',
    'project_coordination': r'project (?:planning|coordination|management)|projekt(?:planung|koordination|management|organisation)',
    'delivery': r'project delivery|projektumsetzung|projektfortschritt|development progress|implementation activit|offene themen|interne zuarbeiten',
    'change_enablement': r'change management|veränderungsprozess\w*|neuausrichtung|organisationsstruktur\w*|einführung.{0,35}(?:prozess|arbeitsweis)|implement.{0,25}training|training solutions|knowledge transfer',
    'monitoring': r'\bmonitor\w*|überwach\w*|offene vorgänge|follow.up|nachverfolg\w*',
    'budget': r'\bbudgets?\b|cost control|margenentwicklung|ressourceneinsatz',
    'launch': r'\b(?:go.live|market launch|market entry|commercial release)\b|marktstart|markteinführ\w*|serienreife',
    'reports': r'\breports?\b|reporting|bericht\w*|präsentation\w*|presentations?',
    'user_research': r'user (?:research|feedback|interviews?)|nutzungsdaten|nutzungs- und abbruchdaten|nutzer(?:forschung|feedback|interviews?)|usability',
}
_PATTERNS = {key: re.compile(value, re.I) for key, value in _CONCEPTS.items()}
_DUTY_HEADING = re.compile(
    r'(?:^|[.!?]\s+|🚀\s*)(?:what you’ll be doing|key responsibilities(?: include)?|hier sorgen sie für veränderung|hier sorgst du für veränderung|tu, was du liebst|diese spannenden aufgaben erwarten dich bei uns|'
    r'freue dich auf spannende aufgaben|dein daily business|dafür übernimmst du verantwortung|'
    r'was du bei uns machst|das erwartet dich bei uns|darum geht es konkret)'
    r'[:. ]*', re.I)
_END_HEADING = re.compile(
    r'^(?:was du von uns hast|warum du zu uns passt|hiermit bringen sie sich ein|hiermit bringst du dich ein|about you|you’ll bring|what we offer|our humble expectations|why join|zeig, was du kannst|das bringst du mit|unser perfect match|damit überzeugst du uns|'
    r'dein profil|ihr profil|your profile|what you bring|das solltest du mitbringen|das überzeugt uns|'
    r'das bieten wir|wir bieten|unsere werte|das macht uns besonders)', re.I)
_ACTION_EQUIVALENTS = {
    'translate': r'\b(?:translat\w*|übersetz\w*)\b',
    'prioritize': r'\b(?:prioriti[sz]\w*|priorisier\w*)\b',
    'coordinate': r'\b(?:align\w*|richt\w*\b.{0,60}\baus|organisier\w*|abstimm\w*|stimm\w*\b.{0,55}\bab|koordinier\w*)\b',
    'analyse': r'\b(?:beobacht\w*|auswert\w*|wert\w*\b.{0,70}\baus|bewert\w*|analy[sz]\w*)\b',
    'deliver': r'\b(?:durchführ\w*|führ\w*\b.{0,55}\bdurch|umsetz\w*|implement\w*|bereitstell\w*)\b',
    'improve': r'\b(?:optimier\w*|verbesser\w*|standardisier\w*)\b',
    'track': r'\b(?:überwach\w*|nachhalt\w*|überprüf\w*|track\w*)\b',
    'create': r'\b(?:entwickl\w*|erstell\w*|entwickel\w*|gestalt\w*|konstruier\w*|erarbeit\w*|konzipier\w*)\b',
    'support': r'\b(?:unterstütz\w*|begleit\w*|mitwirk\w*)\b',
    'plan': r'\b(?:plan\w*|steuer\w*|vorbereit\w*|bereit\w*\b.{0,60}\bvor|terminkoordination)\b',
    'prepare': r'\b(?:prepar\w*|bereit\w*\b.{0,65}\bauf|aufbereit\w*)\b',
    'document': r'\b(?:erfass\w*|protokollier\w*|dokumentier\w*|nachweis\w*)\b',
}
_ACTION_EQUIVALENT_PATTERNS = {
    action: re.compile(pattern, re.I)
    for action, pattern in _ACTION_EQUIVALENTS.items()
}
_DUTY_CLAUSE_SPLIT_RE = re.compile(
    r'(?<=[.?!])\s+|\s+(?=(?:Du|Sie|Koordiniere|Verantworte|Unterstütze|Optimiere|Plane)\s+)'
)
_NON_DUTY_LEAD_RE = re.compile(
    r'^(?:we|wir|our|unsere?\w*|about|at [A-Z]|experience|you are|du bist bereit)\b',
    re.I,
)
_OWN = re.compile(r'\b(?:own(?:s|ed|ing)?|decision authority|end.to.end responsibility|verantwortest|verantworten|gesamtverantwortung)\b', re.I)


# A shared generic action is weaker when its object is subject to a distinct
# professional discipline. Domain signals come from the quoted duty itself.
_DOMAIN_PATTERNS = {
    'accounting': re.compile(r'financial accounting|finanzbuchhaltung|jahresabschluss\w*|kostenrechnung', re.I),
    'regulated_validation': re.compile(r'\b(?:GMP|CAPA|DQ|IQ|OQ|PQ|CQV)\b|validier\w*.*(?:pharma|gmp)', re.I),
    'permitting': re.compile(r'genehmigungs(?:management|verfahren)|umweltrecht|dienstbarkeitsvertrag|land (?:use|permits?)', re.I),
    'trade': re.compile(r'zollabwicklung|ursprungszeugnis|customs (?:processing|clearance)|export control', re.I),
}


def _domains(text):
    return frozenset(domain for domain, pattern in _DOMAIN_PATTERNS.items() if pattern.search(text))


def _concepts(text):
    return frozenset(key for key, pattern in _PATTERNS.items() if pattern.search(text))


def _portable_actions(text, actions):
    actions = set(actions)
    actions.update(action for action, pattern in _ACTION_EQUIVALENT_PATTERNS.items() if pattern.search(text))
    return frozenset(actions)


@lru_cache(maxsize=128)
def _facts(sources):
    facts = []
    for source, text, context, responsibility in sources[:100]:
        for quote, _ in _atomic_duties(text[:12000]):
            if _excluded(quote):
                continue
            actions = _portable_actions(quote, _profile_actions(quote))
            if actions:
                bounded = context in {'academic', 'research', 'training'} or _academic(source, quote)
                owner = (responsibility == 'owned' or bool(_OWN.search(quote))) and not bounded and responsibility not in {'assisted', 'coordinated', 'shared'}
                facts.append((source, quote[:1000], actions, _semantic_objects(quote), _concepts(quote),
                              bounded, owner, responsibility == 'assisted', _domains(quote),
                              'academic' if _academic(source, quote) else context or ('professional' if source.startswith(('experience.', 'cv_career_facts.experiences[')) else 'unspecified')))
    return tuple(facts[:300])


def _duty_units(job, legacy_units, description_sentences=None):
    units = list(legacy_units)
    description = job.get('description') or ''
    candidates = {}
    explicit = False
    sentences = _sentences(description) if description_sentences is None else description_sentences
    for quote, scope in sentences:
        heading = _DUTY_HEADING.search(quote)
        if heading:
            # Limit any recovered scope to the next recognizable heading.
            explicit = True
            quote = quote[heading.end():]
        if _END_HEADING.search(quote) or (not heading and scope in {'requirements', 'optional'}):
            explicit = False
            continue
        if scope == 'duties' or explicit:
            for clause in _DUTY_CLAUSE_SPLIT_RE.split(quote):
                for part, _ in _atomic_job_duties(clause):
                    candidates.setdefault(part.casefold().strip(' .'), part)
    if candidates:
        # Prefer posting duties to company introductions and requirements,
        # while retaining explicitly structured feed duties.
        units = [unit for unit in units if unit['job_source'] != 'description' or
                 any(unit['job_quote'] in clause for clause in candidates.values())]
    existing = {row['job_quote'].casefold().strip(' .') for row in units}
    for key, quote in candidates.items():
        if len(units) >= 60:
            break
        if key in existing or _excluded(quote) or other_actor(quote):
            continue
        actions = _portable_actions(quote, _profile_actions(quote))
        if actions and _semantic_objects(quote):
            units.append({'job_quote': quote, 'job_source': 'description', 'actions': actions, 'id': ''})
            existing.add(key)
    return units[:60]


def analyze_duty_units(job, task_units, description_sentences=None):
    analyzed, seen = [], set()
    for task in _duty_units(job, task_units, description_sentences):
        quote = task['job_quote']
        if _NON_DUTY_LEAD_RE.match(quote):
            continue
        actions = _portable_actions(quote, task['actions'])
        if not actions or len(quote.split()) < 3:
            continue
        objects, concepts = _semantic_objects(quote), _concepts(quote)
        # Keep distinct deliverables and treat repeated versions of the same
        # observable action and object as one requirement.
        key = (actions, frozenset(objects))
        if key in seen:
            continue
        seen.add(key)
        authority = 'own' in actions or bool(_OWN.search(quote))
        domains = _domains(quote)
        analyzed.append((task, actions, objects, concepts, authority, domains))
    return _domains(job.get('title') or ''), analyzed


def assess_duties(job, sources, legacy_alignment, *, description_sentences=None, analysis=None):
    facts = _facts(sources)
    original = {r['id']: r for r in legacy_alignment['requirements']}
    rows = []
    if analysis is None:
        analysis = analyze_duty_units(job, legacy_alignment['requirements'], description_sentences)
    role_domains, units = analysis
    for task, actions, objects, concepts, authority, domains in units:
        quote = task['job_quote']
        specialized = bool(concepts & {'budget', 'launch'})
        best = (0., '', '', 'unspecified')
        old = original.get(task['id'], {})
        if old.get('status') in {'supported', 'transferable'} and not authority and not specialized and not (domains or role_domains):
            direct = old['status'] == 'supported' and old.get('evidence_context') not in {'academic', 'research'}
            best = (.85 if direct else .35,
                    old['evidence_source'], old['evidence_quote'], old['evidence_context'])
        for source, evidence, verbs, products, tags, bounded, owner, assisted, evidence_domains, context in facts:
            shared = concepts & tags
            common = objects & products
            if not shared and len(common) < min(2, len(objects)):
                continue
            if not shared and not common:
                continue
            score = 0.
            if shared:
                # Require a shared work product. Multiple qualifying phrases
                # describing one product do not expand its coverage.
                score = .55 * len(shared) / len(concepts)
                if actions & verbs:
                    score += .25
                if len(shared) >= 2 and actions & verbs:
                    score += .15
            elif actions & verbs:
                score = .55 * len(common) / max(1, len(objects))
            if specialized and not (concepts & {'budget', 'launch'}) <= tags:
                score = min(score, .55)
            if shared and actions & verbs and not specialized and not authority:
                # Exact portable work products retain useful evidence when
                # the posting adds project-specific modifiers or examples.
                score = max(score, .60)
            if domains - evidence_domains:
                score = min(score, .30)
            elif role_domains - evidence_domains:
                score = min(score, .45)
            if bounded or (authority and not owner):
                score = min(score, .45)
            elif assisted and 'support' not in actions:
                score = min(score, .60)
            if score > best[0]:
                best = score, source, evidence, context
        value, source, evidence, context = best
        rows.append({'job_source': task['job_source'], 'job_quote': quote,
                     'concepts': sorted(concepts), 'domains': sorted(domains), 'responsibility': 'owned' if authority else 'unspecified',
                     'weight': 2 if authority or concepts else 1,
                     'status': 'direct' if value >= .75 else 'transferable' if value else 'unverified',
                     'evidence_source': source, 'evidence_quote': evidence,
                     'evidence_context': context, 'credit': round(value, 3)})
    weight = sum(row['weight'] for row in rows)
    score = round(100 * sum(row['credit'] * row['weight'] for row in rows) / weight) if weight else None
    return {'score': score, 'duties': rows, 'coverage': round(sum(bool(row['credit']) for row in rows) / len(rows), 3) if rows else 0}
