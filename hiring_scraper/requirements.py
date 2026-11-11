"""Bounded requirement text and qualification evidence, without CV inference.

Display text can remain compact; requirement analysis keeps blocks and bullets so
an optional bullet or employer benefit cannot change the next applicant clause.
"""
from __future__ import annotations

import re
from collections import deque
from itertools import islice
from hiring_scraper.pages import Document


class RequirementText(str):
    """Display-compatible text retaining bounded HTML heading structure."""
    def __new__(cls, value, heading_levels=(), heading_positions=()):
        result = super().__new__(cls, value)
        result.heading_levels = tuple(heading_levels)
        result.heading_positions = tuple(heading_positions)
        return result


class _Blocks(Document):
    BLOCKS = {'p', 'div', 'li', 'ul', 'ol', 'section', 'article', 'main',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'tr'}

    def __init__(self):
        super().__init__()
        self.heading_levels = []
        self.active_heading = None

    def handle_starttag(self, tag, attrs):
        if re.fullmatch(r'h[1-6]', tag) and not self.hidden:
            self.active_heading = (int(tag[1]), [])
        if tag in self.BLOCKS and not self.hidden:
            self.text.append('\n')
        if re.fullmatch(r'h[1-6]', tag) and not self.hidden:
            self.text.append('\ue000' + tag[1] + '\ue001')
        super().handle_starttag(tag, attrs)

    def handle_data(self, data):
        data = data.replace('\ue000', '').replace('\ue001', '')
        if self.active_heading is not None and not self.hidden:
            self.active_heading[1].append(data)
        super().handle_data(data)

    def handle_endtag(self, tag):
        if re.fullmatch(r'h[1-6]', tag) and self.active_heading is not None:
            level, words = self.active_heading
            self.heading_levels.append((' '.join(''.join(words).split()), level))
            self.active_heading = None
        super().handle_endtag(tag)
        if tag in self.BLOCKS and not self.hidden:
            self.text.append('\n')


def structured_text(value) -> str:
    if not isinstance(value, str):
        return ''
    if isinstance(value, RequirementText):
        return value
    value = value.replace('\ue000', '').replace('\ue001', '')
    chunks = [value] if len(value) <= 240000 else [value[:120000], value[-120000:]]
    texts, heading_levels = [], []
    for chunk in chunks:
        doc = _Blocks()
        doc.feed(chunk)
        texts.append(''.join(doc.text))
        heading_levels.extend(doc.heading_levels)
    lines = [' '.join(line.split()) for line in '\n'.join(texts).splitlines()]
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    marked_headings = {line.strip() for line in result.splitlines() if '\ue000' in line}
    result = '\n'.join(_logical_lines(result, marked_headings))
    # Track the actual heading occurrence through normalization. Equal body text
    # and punctuation-bearing headings must never borrow one another's metadata.
    parts, heading_positions, cursor, length = [], [], 0, 0
    for marker in re.finditer(r'\ue000([1-6])\ue001', result):
        part = result[cursor:marker.start()]
        parts.append(part)
        length += len(part)
        heading_positions.append((length, int(marker[1])))
        cursor = marker.end()
    parts.append(result[cursor:])
    result = ''.join(parts)
    if len(result) > 240000:
        tail_start = len(result) - 120000
        heading_positions = [(offset if offset < 120000 else offset - tail_start + 120001, level)
                             for offset, level in heading_positions
                             if offset < 120000 or offset >= tail_start]
        result = result[:120000] + '\n' + result[-120000:]
    return RequirementText(result, heading_levels, heading_positions)



def structured_requirement_texts(metadata: dict, prefix: str):
    """Retain leaf provenance and required/preferred scope in structured feeds.

    Unknown fields retain inherited scope; they are never promoted to mandatory.
    Traversal is bounded independently of the scraper's metadata size.
    """
    stack = [(metadata, prefix, 0)]
    visited = 0
    while stack and visited < 512:
        value, path, depth = stack.pop()
        visited += 1
        if depth > 10:
            continue
        if isinstance(value, dict):
            children = []
            for key, child in islice(value.items(), 80):
                children.append((child, path + '.' + str(key), depth + 1))
            stack.extend(reversed(children))
        elif isinstance(value, list):
            stack.extend(reversed([(child, f'{path}[{index}]', depth + 1)
                                   for index, child in enumerate(value[:80])]))
        elif isinstance(value, str) and value.strip():
            yield path, structured_text(value)



def structured_source_scope(source: str) -> str:
    """Decode recognized structured-feed modality without changing source text."""
    parts = set(re.sub(r'\[\d+\]', '', source).casefold().split('.'))
    if parts & {'preferred', 'optional', 'nice_to_have', 'desired'}:
        return 'optional'
    if parts & {'required', 'mandatory', 'must_have', 'qualifications',
                'educationrequirements', 'experiencerequirements'}:
        return 'requirements'
    if parts & {'responsibilities', 'duties'}:
        return 'duties'
    return 'neutral'


_REQUIRED_HEADING = re.compile(
    r'^(?:(?:ihr|dein|your)\s+(?:profil|profile|qualifications|requirements)|'
    r'zwingend\w*(?:\s+voraussetzungen)?|voraussetzung(?:en)?|mandatory qualifications|essential requirements|was zählt|(?:ihre|deine)\s+qualifikation(?:en)?|required qualifications|damit begeistern sie uns|must.have|requirements|qualifications|anforderungen|'
    r'(?:das|was)\s+(?:sie|du)\s+mitbring(?:en|st)|was wir erwarten|'
    r'das bringen sie mit|das bringst du mit|was bringen sie mit|was bringst du mit|überzeuge uns mit (?:deinen|ihren) qualifikationen|profil|qualifikation(?:en)?|fachliche anforderungen)\b', re.I)
_OTHER_HEADING = re.compile(
    r'^(?:(?:ihr|ihre|dein|deine|your|unsere)\s+(?:aufgaben(?:bereich)?|tasks|responsibilities|benefits)|'
    r'aufgaben|responsibilities|duties|benefits|perks|wir bieten|we offer|what we offer|was wir bieten|dafür bieten wir ihnen|'
    r'das bieten wir|unser angebot|über uns|about us|about (?:the|our) company|core work|your role|kontakt|contact|bewerbung|tasks|'
    r'das sind (?:deine|ihre) aufgaben|diese herausforderungen|' 
    r'ihre vorteile|deine vorteile|deine benefits|ihre benefits|das erwartet (?:sie|dich))\b', re.I)
_OPTIONAL_HEADING = re.compile(r'^(?:preferred qualifications|nice.to.have|optional|wünschenswert|von vorteil)\s*[:.]*$', re.I)


def _logical_lines(text: str, heading_names=()) -> list[str]:
    # Join source soft-wraps only inside a bullet; blank lines/headings retain
    # their boundaries. This is distinct from flattening separate requirements.
    logical_lines = []
    bullet = False
    for line in text.splitlines():
        stripped = line.strip()
        heading = stripped in heading_names or bool(_REQUIRED_HEADING.search(stripped.strip('*# ')) or
                       _OTHER_HEADING.search(stripped.strip('*# ')) or
                       _OPTIONAL_HEADING.search(stripped.strip('*# ')))
        starts_bullet = bool(re.match(r'^[-*•]\s+', stripped))
        if bullet and stripped and not starts_bullet and not heading and not stripped.startswith(('#', '**')):
            logical_lines[-1] += ' ' + stripped
        else:
            logical_lines.append(line)
            bullet = starts_bullet
        if not stripped:
            bullet = False
    return logical_lines


def scoped_sentences(text: str, *, structured: bool = False, initial_scope: str | None = None):
    scope = initial_scope or ('requirements' if structured else 'neutral')
    positions = deque(getattr(text, 'heading_positions', ()))
    section_stack = []
    normalized = str(text) if isinstance(text, RequirementText) else '\n'.join(_logical_lines(text))
    cursor = 0
    instruction_block = False
    for part in re.split(r'(?<=[.!?])(?<!mind\.)(?<!min\.)\s+|\s*[;\n]\s*', normalized, flags=re.I):
        start = normalized.find(part, cursor)
        cursor = start + len(part)
        sentence = re.sub(r'^[\s#*•\-]+|[*#]+$', '', part).strip()
        if not sentence:
            continue
        level = None
        while positions and positions[0][0] < cursor:
            offset, candidate_level = positions.popleft()
            if offset >= start:
                level = candidate_level
        if _SOURCE_ROLE_HEADER.fullmatch(sentence):
            instruction_block = True
            continue
        if instruction_block:
            employer_heading = (_REQUIRED_HEADING.search(sentence) or
                                _OTHER_HEADING.search(sentence) or
                                _OPTIONAL_HEADING.search(sentence))
            if not employer_heading:
                continue
            instruction_block = False
        if is_source_instruction(sentence):
            continue
        if level is not None:
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            scope = section_stack[-1][1] if section_stack else (initial_scope or 'neutral')
        if _REQUIRED_HEADING.search(sentence):
            scope = 'requirements'
        elif _OTHER_HEADING.search(sentence):
            task_heading = (not re.match(r'^(?:contact|kontakt|bewerbung)\b', sentence, re.I) and
                            re.search(r'aufgaben|tasks|responsibilities|duties|^core work|^your role', sentence, re.I))
            scope = 'duties' if task_heading else 'benefits'
        elif _OPTIONAL_HEADING.search(sentence):
            scope = 'optional'
        elif scope == 'neutral' and re.match(r'^abgeschlossen\w*\b', sentence, re.I) and APPLICANT_CUE.search(sentence):
            scope = 'requirements'
        if level is not None:
            section_stack.append((level, scope))
        yield sentence, scope


_SOURCE_ROLE_HEADER = re.compile(r'(?:\[?system\]?|\[(?:assistant|developer)\])\s*:', re.I)
_SOURCE_INSTRUCTION = re.compile(
    r'(?:^|[\n.!?]\s*)(?:(?:\[?system\]?|\[(?:assistant|developer)\])\s*:|'
    r'(?:copied\s+)?prompt[ -]injection\s*:)|'
    r'\b(?:ignore|disregard|override)\s+(?:(?:all|the)\s+)?'
    r'(?:previous|prior|system|developer)\s+(?:instructions?|prompts?)\b|'
    r'\b(?:assistant|developer)\s*:\s*you\s+(?:must|should)\s+require\b|'
    r'\b(?:ignoriere|ignorieren Sie)\s+(?:alle\s+)?(?:vorherigen|bisherigen)\s+Anweisungen\b', re.I)


def is_source_instruction(text: str) -> bool:
    """Identify source text addressing the evaluator, not the applicant."""
    return bool(_SOURCE_INSTRUCTION.search(text))


_POLARITY_BOUNDARY = re.compile(
    r'[;\n]|(?<=[.!?])\s+|\b(?:but|aber|while|whereas|jedoch)\b|'
    r'(?:,\s*|\b(?:and|und)\s+)(?=(?:[\w-]+\s+){1,8}'
    r'(?:is|are|ist|sind)\s+(?:(?:not|nicht)\s+)?'
    r'(?:required|necessary|mandatory|optional|preferred|erforderlich|notwendig|wünschenswert)\b)|'
    r'(?:,\s*|\b(?:and|und)\s+)(?=(?:(?:a|an|the|eine?|ein|der|die|das)\s+)?'
    r'(?:PMP|PRINCE2|Scrum|certificat\w*|zertifizier\w*|'
    r'(?:completed\s+)?(?:bachelor|master|degree)|'
    r'(?:abgeschlossen\w*\s+)?Studium|Immatrikulation|GMP)\b)', re.I)
_OPTIONAL_QUALIFICATION = re.compile(
    r'\b(?:optional|preferred|desired|nice.to.have|idealerweise|wünschenswert|'
    r'bevorzugt|willkommen|von Vorteil|a plus)\b', re.I)
_WAIVED_QUALIFICATION = re.compile(
    r'\b(?:not|nicht)\s+(?:strictly\s+|zwingend\s+)?'
    r'(?:required|necessary|mandatory|essential|needed|erforderlich|notwendig|vorausgesetzt)\b|'
    r'\bkein\w*\s+(?:zwingende\s+)?Voraussetzung\b', re.I)
_TRAILING_MODALITY = re.compile(
    r'\b(?:required|necessary|mandatory|essential|needed|optional|preferred|desired|'
    r'erforderlich|notwendig|vorausgesetzt|wünschenswert|bevorzugt|willkommen|'
    r'Voraussetzung|Vorteil|plus)(?:\s+(?:for|in|at|für|bei)\s+'
    r'(?:[\w-]+\s*){1,6})?\s*$', re.I)


def _polarity_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left, right = 0, len(text)
    for boundary in _POLARITY_BOUNDARY.finditer(text):
        # A trailing predicate can govern a coordinated noun list. Split
        # coordination only after the first item has its own modality.
        if (re.match(r'\s*(?:,|and\b|und\b)', boundary.group(), re.I) and
                not _TRAILING_MODALITY.search(text[left:boundary.start()])):
            continue
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= end:
            right = boundary.start()
            break
    return left, right


def requirement_polarity(text: str, start: int = 0, end: int | None = None) -> str:
    """Classify modality around a literal match, retaining independent siblings.

    ``required`` means no local waiver/preference was found; callers still need
    their normal applicant/section requirement checks. Offsets refer to the
    original text; this helper never rewrites citable evidence.
    """
    if is_source_instruction(text):
        return 'instruction'
    end = start if end is None else end
    left, right = _polarity_bounds(text, start, end)
    local = text[left:right]
    if _WAIVED_QUALIFICATION.search(local):
        return 'waived'
    if (re.search(r'\bneither\b.+\bnor\b', local, re.I) and
            re.search(r'\b(?:required|necessary|mandatory|needed)\b', local, re.I)):
        return 'waived'
    # A negative task verb ("prevent failures") is not a waiver.
    before, after = text[left:start], text[end:right]
    if (re.search(r'\b(?:no|kein\w*)\s+(?:[\w-]+\s+){0,6}$', before, re.I) and
            re.search(r'\b(?:required|necessary|mandatory|needed|erforderlich|notwendig|vorausgesetzt)\b', after, re.I)):
        return 'waived'
    if _OPTIONAL_QUALIFICATION.search(local):
        return 'optional'
    return 'required'


DEGREE = re.compile(r'\b(?:degree|bachelor\w*|master\w*|doctorate|phd|studium|hochschulstudium|hochschulabschluss|abschluss|ausbildung)\b', re.I)
DEGREE_FIELDS = {
    'electrical engineering': r'elektrotechnik|electrical engineering|elektroingenieur\w*',
    'mechanical engineering': r'maschinenbau|mechanical engineering',
    'civil engineering': r'bauingenieur\w*|bauwesen|civil engineering',
    'computer science': r'informatik|computer science|wirtschaftsinformatik',
    'business administration': r'betriebswirtschaft\w*|business administration|bwl|wirtschaftswissenschaft\w*',
    'life sciences': r'naturwissenschaft\w*|life sciences?',
    'health sciences': r'heilberuf\w*|health sciences?|healthcare|medizin',
    'chemistry': r'chemie|chemistry', 'pharmacy': r'pharmazie|pharmacy',
    'innovation engineering': r'innovation and development engineering|innovationsengineering',
    'engineering': r'ingenieurwissenschaft\w*|ingenieurwesen|engineering',
}
DOMAIN = re.compile(
    r'\b(?:medical devices?|medizintechnik|PV|photovoltaik|photovoltaics?|solar|'
    r'electrical|elektrotechnik|construction|bauwesen|bauleitung|bauingenieur\w*|'
    r'insurance|underwriting|versicher\w*|pharma\w*|GMP|GCP|GxP|gebäudeautomation|steuer. und regelungstechnik|HKL|VOB|energietechnik|automatisierungstechnik|EMSR|SCADA|versorgungstechnik|SHK|hochbau|tiefbau|ingenieurbau|verkehrsplanung|sicherheitstechnik|SAP(?:\s+[A-Z]{2,5})?|'
    r'S/4HANA|HANA|cloud infrastructure|rechenzentrum)\b', re.I)
CERTIFICATE = re.compile(r'\b(?:PMP|PRINCE2|Scrum\s+(?:Master|certification)|certificate|certification|zertifikat\w*|zertifizier(?:ung|t))\b', re.I)
WAIVED_CERTIFICATE = re.compile(
    r'\b(?:no|kein\w*)\s+(?:[\w-]+\s+){0,3}'
    r'(?:certification|zertifizierung)\s+(?:(?:is|are|ist|sind)\s+)?'
    r'(?:required|necessary|erforderlich|notwendig)\b', re.I)
TENURE = re.compile(r'\b(?:professional\s+(?:experience|\w+\s+experience)|berufserfahrung|mehrjährige\w*\s+erfahrung|extensive\s+experience|langjährige\w*\s+erfahrung)\b', re.I)
APPLICANT_CUE = re.compile(r'\b(?:abgeschlossen\w*\s+(?:\w+\s+){0,3}(?:studium|hochschulstudium|masterstudium|bachelorstudium|ausbildung)|(?:you have|you possess|sie verfügen|du verfügst|verfügst über|verfügen über)\s+(?:\w+\s+){0,4}(?:experience|erfahrung|berufserfahrung|kenntnisse))\b', re.I)
ENROLMENT = re.compile(r'\b(?:immatrikulation|immatrikuliert\w*|eingeschrieben\w*|enrolled|student enrolment|student enrollment|laufende[smr]?\s+studium)\b', re.I)


def qualification_rows(clause: str, source: str) -> list[dict]:
    rows = []
    if is_source_instruction(clause):
        return rows

    def row(kind, value, hit, **extra):
        if requirement_polarity(clause, hit.start() if hit else 0, hit.end() if hit else 0) != 'required':
            return
        rows.append({'kind': kind, 'value': value, 'source': source,
                     'evidence': clause[:500], **extra})

    if (hit := ENROLMENT.search(clause)):
        row('enrolment', 'Current student enrolment', hit)
    degree_spans = set()
    for hit in DEGREE.finditer(clause):
        bounds = _polarity_bounds(clause, hit.start(), hit.end())
        if bounds in degree_spans:
            continue
        degree_spans.add(bounds)
        degree_clause = clause[bounds[0]:bounds[1]]
        level = ('doctorate' if re.search(r'phd|doctorate|promotion', degree_clause, re.I) else
                 'bachelor' if re.search(r'bachelor', degree_clause, re.I) and re.search(r'\b(?:or|oder)\b', degree_clause, re.I) else
                 'master' if re.search(r'master', degree_clause, re.I) else
                 'vocational' if re.search(r'ausbildung', degree_clause, re.I) and not re.search(r'studium|degree|bachelor|master', degree_clause, re.I) else 'bachelor')
        # Broad engineering is not a second alternative to electrical engineering.
        fields = [name for name, pattern in DEGREE_FIELDS.items()
                  if re.search(r'\b(?:' + pattern + r')\b', degree_clause, re.I)]
        if len(fields) > 1 and 'engineering' in fields:
            fields.remove('engineering')
        field_phrase = re.search(r'(?:degree\s+in|studium\s+(?:in|der|im Bereich|in der Fachrichtung))\s+(.+)', degree_clause, re.I)
        unknown_field = field_phrase.group(1).strip(' .') if field_phrase and not fields else None
        row('education', 'Degree', hit, level=level, fields=fields, unknown_field=unknown_field,
            alternative=bool(re.search(r'\b(?:or|oder|alternativ)\b', degree_clause, re.I)))
    if re.match(r'^(?:Meister|Techniker|Ingenieur)\b', clause, re.I) and re.search(r'im Bereich', clause, re.I):
        row('qualification', 'Specialist technical qualification', None)
    if re.search(r'\bSprachniveau\s+(?:C1|C2)\b', clause, re.I):
        row('qualification', 'Unspecified language level', None)
    for hit in CERTIFICATE.finditer(clause):
        if not any(waiver.start() <= hit.start() and hit.end() <= waiver.end()
                   for waiver in WAIVED_CERTIFICATE.finditer(clause)):
            row('certification', hit.group(), hit)
    for hit in DOMAIN.finditer(clause):
        # Suppress only the domain token inside a recognized degree subject; a
        # separate qualification such as GMP experience must remain a gap.
        left, right = _polarity_bounds(clause, hit.start(), hit.end())
        degree_subject = bool(DEGREE.search(clause[left:right])) and any(
            subject.start() <= hit.start() and hit.end() <= subject.end()
            for pattern in DEGREE_FIELDS.values()
            for subject in re.finditer(r'\b(?:' + pattern + r')\b', clause, re.I))
        if not degree_subject:
            row('domain', hit.group(), hit)
    return rows


def education_satisfied(requirement: dict, profile: dict) -> bool:
    ranks = {'vocational': 0, 'bachelor': 1, 'master': 2, 'doctorate': 3}
    required = requirement.get('level', 'bachelor')
    entries = list(profile.get('education') or [])
    for entry in (profile.get('cv_career_facts') or {}).get('education') or []:
        title = str(entry.get('title') or '')
        evidence = title + ' ' + str(entry.get('description') or '')
        if (entry.get('current') is not False or not entry.get('end') or
                re.search(r'incomplete|withdrawn|withdrew|'
                          r'\b(?:not|never)\s+(?:\w+\s+){0,2}(?:graduat\w*|complet\w*|earn\w*|award\w*)|'
                          r'\bno\s+(?:completed\s+)?degree|without.*degree|nicht abgeschlossen|abgebrochen|kein|ohne', evidence, re.I)):
            continue
        if entry.get('completed') is not True and not re.search(
                r'\b(?:graduated|(?:awarded|earned)\s+(?:(?:this|the|a|an|my)\s+)?'
                r'(?:degree|bachelor|master|doctorate|phd)|'
                r'(?:successfully\s+)?completed\s+(?:(?:this|the|my)\s+)?(?:degree|studies|bachelor|master)|'
                r'degree\s+(?:(?:was|is)\s+)?(?:completed|awarded)|'
                r'(?:studium|abschluss)\s+(?:erfolgreich\s+)?(?:abgeschlossen|absolviert)|'
                r'abgeschlossen\w*\s+(?:studium|hochschulstudium))\b', evidence, re.I):
            continue
        level = next((level for level, pattern in (
            ('doctorate', r'\b(?:phd|doctorate)\b'), ('master', r'\bmaster\b'),
            ('bachelor', r'\bbachelor\b')) if re.search(pattern, title, re.I)), None)
        if level:
            entries.append({**entry, 'level': level, 'field': title})
    for education in entries:
        status = str(education.get('status', '')).casefold().replace('-', '_').replace(' ', '_')
        if (education.get('completed') is False or education.get('current') is True or
                status in {'in_progress', 'ongoing', 'enrolled', 'withdrawn', 'incomplete', 'planned'}):
            continue
        level = education.get('level')
        if required == 'vocational' and level != 'vocational':
            continue
        if ranks.get(level, -1) < ranks.get(required, 1):
            continue
        unknown_field = requirement.get('unknown_field')
        if unknown_field and unknown_field.casefold() not in str(education.get('field', '')).casefold():
            continue
        fields = requirement.get('fields') or []
        candidate_field = str(education.get('field', ''))
        satisfied = [bool(re.search(r'\b(?:' + DEGREE_FIELDS[field] + r')\b', candidate_field, re.I)) for field in fields]
        if not fields or (any(satisfied) if requirement.get('alternative') else all(satisfied)):
            return True
    return False
