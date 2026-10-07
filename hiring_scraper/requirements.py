"""Bounded requirement text and qualification evidence, without CV inference.

Display text can remain compact; requirement analysis keeps blocks and bullets so
an optional bullet or employer benefit cannot change the next applicant clause.
"""
from __future__ import annotations

import re
from hiring_scraper.pages import Document


class _Blocks(Document):
    BLOCKS = {'p', 'div', 'li', 'ul', 'ol', 'section', 'article', 'main',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'tr'}

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCKS and not self.hidden:
            self.text.append('\n')
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag in self.BLOCKS and not self.hidden:
            self.text.append('\n')


def structured_text(value) -> str:
    if not isinstance(value, str):
        return ''
    chunks = [value] if len(value) <= 240000 else [value[:120000], value[-120000:]]
    texts = []
    for chunk in chunks:
        doc = _Blocks()
        doc.feed(chunk)
        texts.append(''.join(doc.text))
    lines = [' '.join(line.split()) for line in '\n'.join(texts).splitlines()]
    result = '\n'.join(line for line in lines if line)
    return result if len(result) <= 240000 else result[:120000] + '\n' + result[-120000:]


_REQUIRED_HEADING = re.compile(
    r'^(?:(?:ihr|dein|your)\s+(?:profil|profile|qualifications|requirements)|'
    r'(?:ihre|deine)\s+qualifikationen|required qualifications|requirements|qualifications|anforderungen|'
    r'(?:das|was)\s+(?:sie|du)\s+mitbring(?:en|st)|was wir erwarten|'
    r'das bringen sie mit|das bringst du mit|was bringen sie mit|was bringst du mit|überzeuge uns mit (?:deinen|ihren) qualifikationen|profil|qualifikation(?:en)?|fachliche anforderungen)\b', re.I)
_OTHER_HEADING = re.compile(
    r'^(?:(?:ihr|ihre|dein|deine|your|unsere)\s+(?:aufgaben|tasks|responsibilities|benefits)|'
    r'responsibilities|duties|benefits|perks|wir bieten|we offer|what we offer|'
    r'das bieten wir|unser angebot|über uns|about us|kontakt|contact|bewerbung|tasks|'
    r'das sind (?:deine|ihre) aufgaben|diese herausforderungen|' 
    r'ihre vorteile|deine vorteile|deine benefits|ihre benefits|das erwartet (?:sie|dich))\b', re.I)
_OPTIONAL_HEADING = re.compile(r'^(?:preferred qualifications|nice.to.have|optional|wünschenswert|von vorteil)\s*[:.]*$', re.I)


def scoped_sentences(text: str, *, structured: bool = False):
    scope = 'requirements' if structured else 'neutral'
    for part in re.split(r'(?<=[.!?])\s+|\s*[;\n]\s*', text):
        sentence = re.sub(r'^[\s#*•\-]+|[*#]+$', '', part).strip()
        if not sentence:
            continue
        if _REQUIRED_HEADING.search(sentence):
            scope = 'requirements'
        elif _OTHER_HEADING.search(sentence):
            scope = 'duties' if re.search(r'aufgaben|tasks|responsibilities|duties', sentence, re.I) else 'benefits'
        elif _OPTIONAL_HEADING.search(sentence):
            scope = 'optional'
        yield sentence, scope


DEGREE = re.compile(r'\b(?:degree|bachelor\w*|master\w*|doctorate|phd|studium|hochschulabschluss|abschluss|ausbildung)\b', re.I)
DEGREE_FIELDS = {
    'electrical engineering': r'elektrotechnik|electrical engineering|elektroingenieur\w*',
    'mechanical engineering': r'maschinenbau|mechanical engineering',
    'civil engineering': r'bauingenieur\w*|bauwesen|civil engineering',
    'computer science': r'informatik|computer science|wirtschaftsinformatik',
    'business administration': r'betriebswirtschaft\w*|business administration|bwl|wirtschaftswissenschaft\w*',
    'chemistry': r'chemie|chemistry', 'pharmacy': r'pharmazie|pharmacy',
    'innovation engineering': r'innovation and development engineering|innovationsengineering',
    'engineering': r'ingenieurwissenschaft\w*|ingenieurwesen|engineering',
}
DOMAIN = re.compile(
    r'\b(?:medical devices?|medizintechnik|PV|photovoltaik|photovoltaics?|solar|'
    r'electrical|elektrotechnik|construction|bauwesen|bauleitung|bauingenieur\w*|'
    r'insurance|underwriting|versicher\w*|pharma\w*|GMP|GxP|energietechnik|automatisierungstechnik|EMSR|SCADA|versorgungstechnik|SHK|hochbau|tiefbau|ingenieurbau|verkehrsplanung|sicherheitstechnik|SAP(?:\s+[A-Z]{2,5})?|'
    r'S/4HANA|HANA|cloud infrastructure|rechenzentrum)\b', re.I)
CERTIFICATE = re.compile(r'\b(?:PMP|PRINCE2|Scrum\s+(?:Master|certification)|certification|zertifizier(?:ung|t))\b', re.I)
TENURE = re.compile(r'\b(?:professional\s+(?:experience|\w+\s+experience)|berufserfahrung|mehrjährige\w*\s+erfahrung|extensive\s+experience|langjährige\w*\s+erfahrung)\b', re.I)
APPLICANT_CUE = re.compile(r'\b(?:abgeschlossen\w*\s+(?:\w+\s+){0,3}(?:studium|masterstudium|bachelorstudium|ausbildung)|(?:you have|you possess|sie verfügen|du verfügst|verfügst über|verfügen über)\s+(?:\w+\s+){0,4}(?:experience|erfahrung|berufserfahrung|kenntnisse))\b', re.I)
ENROLMENT = re.compile(r'\b(?:immatrikuliert\w*|eingeschrieben\w*|enrolled|student enrolment|student enrollment|laufende[smr]?\s+studium)\b', re.I)


def qualification_rows(clause: str, source: str) -> list[dict]:
    rows = []

    def row(kind, value, hit, **extra):
        rows.append({'kind': kind, 'value': value, 'source': source,
                     'evidence': clause[:500], **extra})

    if (hit := ENROLMENT.search(clause)):
        row('enrolment', 'Current student enrolment', hit)
    if (hit := DEGREE.search(clause)):
        level = ('doctorate' if re.search(r'phd|doctorate|promotion', clause, re.I) else
                 'bachelor' if re.search(r'bachelor', clause, re.I) and re.search(r'\b(?:or|oder)\b', clause, re.I) else
                 'master' if re.search(r'master', clause, re.I) else
                 'vocational' if re.search(r'ausbildung', clause, re.I) and not re.search(r'studium|degree|bachelor|master', clause, re.I) else 'bachelor')
        # Broad engineering is not a second alternative to electrical engineering.
        fields = [name for name, pattern in DEGREE_FIELDS.items()
                  if re.search(r'\b(?:' + pattern + r')\b', clause, re.I)]
        if len(fields) > 1 and 'engineering' in fields:
            fields.remove('engineering')
        field_phrase = re.search(r'(?:degree\s+in|studium\s+(?:in|der|im Bereich|in der Fachrichtung))\s+(.+)', clause, re.I)
        unknown_field = field_phrase.group(1).strip(' .') if field_phrase and not fields else None
        row('education', 'Degree', hit, level=level, fields=fields, unknown_field=unknown_field,
            alternative=bool(re.search(r'\b(?:or|oder|alternativ)\b', clause, re.I)))
    for hit in CERTIFICATE.finditer(clause):
        row('certification', hit.group(), hit)
    for hit in DOMAIN.finditer(clause):
        # Suppress only the domain token inside a recognized degree subject; a
        # separate qualification such as GMP experience must remain a gap.
        degree_subject = bool(DEGREE.search(clause)) and any(
            subject.start() <= hit.start() and hit.end() <= subject.end()
            for pattern in DEGREE_FIELDS.values()
            for subject in re.finditer(r'\b(?:' + pattern + r')\b', clause, re.I))
        if not degree_subject:
            row('domain', hit.group(), hit)
    return rows


def education_satisfied(requirement: dict, profile: dict) -> bool:
    ranks = {'vocational': 0, 'bachelor': 1, 'master': 2, 'doctorate': 3}
    required = requirement.get('level', 'bachelor')
    for education in profile.get('education') or []:
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
