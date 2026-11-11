"""Versioned, evidence-backed job signals and an explainable local matching baseline.

Scores express overlap with a user's interests, never a probability of being hired.
Skill mentions are not automatically treated as mandatory qualifications.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from urllib.parse import urljoin, urlsplit, urlunsplit

from hiring_scraper.task_alignment import align_tasks, other_actor, task_units
from hiring_scraper.election_data import job_city_vote_results
from hiring_scraper.pages import Document
from hiring_scraper.requirements import (structured_source_scope, structured_requirement_texts, structured_text, scoped_sentences, qualification_rows,
                                         education_satisfied, TENURE, APPLICANT_CUE, DOMAIN, DEGREE,
                                         requirement_polarity, is_source_instruction)

VERSION = 'rules-v45-proportional-task-evidence'
SKILLS = {
    'Python': ['python'], 'JavaScript': ['javascript', 'js'], 'TypeScript': ['typescript'],
    'Java': ['java'], 'C++': ['c++'], 'C#': ['c#'], '.NET': ['.net', 'dotnet'],
    'PHP': ['php'], 'Ruby': ['ruby'], 'Rust': ['rust'], 'Go': ['golang'],
    'React': ['react', 'reactjs'], 'Angular': ['angular'], 'Vue': ['vue', 'vuejs'],
    'SQL': ['sql'], 'PostgreSQL': ['postgresql', 'postgres'], 'MySQL': ['mysql'],
    'MongoDB': ['mongodb'], 'Redis': ['redis'], 'Linux': ['linux'],
    'Docker': ['docker'], 'Kubernetes': ['kubernetes', 'k8s'], 'Terraform': ['terraform'],
    'AWS': ['aws', 'amazon web services'], 'Azure': ['azure'], 'GCP': ['gcp', 'google cloud'],
    'Git': ['git'], 'CI/CD': ['ci/cd', 'continuous integration', 'continuous delivery'],
    'Django': ['django'], 'FastAPI': ['fastapi'], 'Spring': ['spring boot', 'spring framework'],
    'Node.js': ['node.js', 'nodejs'], 'REST APIs': ['rest api', 'restful'],
    'Machine learning': ['machine learning', 'maschinelles lernen'], 'PyTorch': ['pytorch'],
    'TensorFlow': ['tensorflow'], 'Data analysis': ['data analysis', 'datenanalyse'],
    'Excel': ['excel'], 'Power BI': ['power bi'], 'Tableau': ['tableau'],
    'SAP': ['sap'], 'Salesforce': ['salesforce'], 'Accounting': ['accounting', 'buchhaltung', 'buchführung', 'buchfuehrung'],
    'Financial reporting': ['financial reporting', 'bilanzierung', 'jahresabschluss'],
    'Payroll': ['payroll', 'lohnabrechnung'], 'DATEV': ['datev'],
    'Project management': ['project management', 'projektmanagement', 'project coordination', 'projektkoordination', 'plan projects', 'coordinate projects', 'manage projects', 'project planning', 'projektplanung', 'koordinieren projekte', 'projekte koordinieren'],
    'Requirements gathering': ['requirements gathering', 'requirements analysis', 'anforderungsanalyse', 'anforderungsmanagement', 'gather requirements', 'collect requirements', 'erheben anforderungen', 'anforderungen erheben'],
    'Project coordination': ['project coordination', 'projektkoordination'],
    'Product development': ['product development', 'produktentwicklung'],
    'Stakeholder coordination': ['stakeholder coordination', 'stakeholder management', 'stakeholder alignment', 'stakeholder-abstimmung', 'stakeholder abstimmung', 'stakeholderkommunikation', 'coordinate stakeholders'],
    'Process improvement': ['process improvement', 'prozessoptimierung', 'prozessverbesserung', 'improve processes', 'optimieren prozesse', 'prozesse optimieren'],
    'KPI monitoring': ['kpi monitoring', 'kpi tracking', 'kennzahlenmonitoring'],
    'Training and onboarding': ['training and onboarding', 'training delivery', 'schulungsdurchführung', 'onboarding coordination'],
    'Training and development': ['training and development', 'learning and development', 'personalentwicklung'],
    'Onboarding': ['onboarding', 'einarbeitung'],
    'User research': ['user research', 'nutzerforschung'],
    'Technical drawings': ['technical drawings', 'technische zeichnungen'],
    'Bill of materials': ['bill of materials', 'stückliste'],
    'Product costing': ['product costing', 'produktkalkulation'],
    'Process mapping': ['process mapping', 'prozessmapping'],
    'Supplier coordination': ['supplier coordination', 'lieferantenkoordination'],
    'Program coordination': ['program coordination', 'programme coordination', 'programmkoordination'],
    'Figma': ['figma'], 'Trello': ['trello'], 'Kanban': ['kanban'],
    'Fusion 360': ['fusion 360'], '3D printing': ['3d printing', '3d-druck'],
    'Prototyping': ['prototyping', 'prototypenbau'],
    'Scrum': ['scrum'], 'Product management': ['product management', 'produktmanagement'],
    'Customer service': ['customer service', 'kundenservice', 'kundenbetreuung'],
    'Sales': ['sales', 'vertrieb'], 'Marketing': ['marketing'], 'SEO': ['seo', 'suchmaschinenoptimierung'],
    'Recruiting': ['recruiting', 'recruitment', 'personalbeschaffung'],
    'CAD': ['cad', 'autocad'], 'SolidWorks': ['solidworks'], 'Electrical engineering': ['electrical engineering', 'elektrotechnik'],
    'Nursing': ['nursing', 'krankenpflege', 'altenpflege'], 'Logistics': ['logistics', 'logistik'],
}
ROLE_GROUPS = [
    ['software developer', 'software engineer', 'softwareentwickler', 'softwareentwicklerin', 'developer', 'backend', 'frontend', 'full stack', 'fullstack'],
    ['data scientist', 'data analyst', 'datenanalyst', 'data science', 'machine learning'],
    ['devops', 'platform engineer', 'cloud engineer', 'systemadministrator', 'system administrator'],
    ['accountant', 'accounting', 'buchhalter', 'buchhaltung', 'finance', 'finanz', 'bilanzbuchhalter'],
    ['sales', 'vertrieb', 'account manager'], ['marketing', 'seo', 'content manager'],
    ['nursing', 'nurse', 'pflege', 'pflegefachkraft'], ['logistics', 'logistik', 'lager'],
    ['project coordinator', 'project coordination', 'project manager', 'project management', 'project lead',
     'project officer', 'pmo', 'projektkoordinator', 'projektkoordinatorin', 'projektmanager',
     'projektmanagerin', 'projektmanagement', 'projektleitung', 'projektleiter', 'projektleiterin',
     'projektassistenz', 'project delivery manager', 'project assistant'],
    ['product development', 'product manager', 'product management', 'product operations',
     'produktentwicklung', 'produktentwickler', 'produktentwicklerin', 'produktmanager', 'produktmanagerin', 'product owner'],
    ['innovation engineer', 'development engineer', 'innovationsingenieur', 'innovationsingenieurin',
     'entwicklungsingenieur', 'entwicklungsingenieurin', 'prototyping engineer', 'innovationsassistent',
     'innovationsassistentin', 'projektingenieur', 'projektingenieurin'],
    ['learning and development', 'l&d coordinator', 'training coordinator', 'personalentwicklung',
     'weiterbildung', 'schulungskoordinator', 'schulungskoordinatorin'],
    ['process improvement', 'quality coordinator', 'prozessoptimierung', 'prozessverbesserung', 'qualitätskoordination'],
    ['program coordinator', 'programme coordinator', 'international partnerships', 'executive assistant',
     'programmkoordinator', 'programmkoordinatorin', 'internationale partnerschaften', 'assistenz der geschäftsführung',
     'programme assistant', 'program assistant', 'assistenz geschäftsleitung', 'assistenz der geschäftsleitung',
     'assistenz geschäftsführung'],
    ['customer service', 'customer operations', 'kundenservice', 'kundenbetreuung'],
    ['business analyst', 'business analysis', 'requirements analyst', 'business analystin',
     'business analyse', 'anforderungsmanager', 'anforderungsmanagerin', 'business process analyst'],
    ['cad designer', 'cad konstrukteur', 'cad konstrukteurin', 'konstrukteur', 'konstrukteurin'],
]
# Immutable role groups make bounded query membership caching safe.
ROLE_GROUPS = tuple(tuple(group) for group in ROLE_GROUPS)
OPTIONAL = re.compile(r'nice.to.have|optional|preferred|idealerweise|wünschenswert|bevorzugt|willkommen|keine? voraussetzung|von vorteil|a plus|not required|nicht erforderlich', re.I)
REQUIREMENT = re.compile(r'\b(required|mandatory|essential|requirement|requirements|your profile|your qualifications|must have|must possess|you need|needs?|need|ihr profil|dein profil|anforderungen|was sie mitbringen|was du mitbringst|wir erwarten|vorausgesetzt\w*|voraussetzung\w*|notwendig\w*|müssen\w*|erforderlich|zwingend|pflicht|mindestens|at least|minimum)\b', re.I)
NON_REQUIREMENT = re.compile(r'\b(we have|our company|our team|we offer|benefits|perks|you receive|you get|you can take|we provide you with|du erhältst|sie erhalten|wir bieten|unser unternehmen|unsere firma|seit \d{4}|common among our clients|spoken by our clients|our clients speak|our customers speak|our team speaks)\b', re.I)
QUANTIFIED_SCRIPTING_LIST = re.compile(
    r'\bmindestens eine dieser Skriptsprachen\s*\(([^()\n]{1,300})\)', re.I)
CEFR_RANK = {'A1': 1, 'A2': 2, 'B1': 3, 'B2': 4, 'C1': 5, 'C2': 6, 'NATIVE': 7}
EXPERIENCE_NUMBER = (
    r'(?:\d{1,2}(?:[.,]\d)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|'
    r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|'
    r'ein(?:e|en|em|er|s)?|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|zehn|elf|'
    r'zwölf|zwoelf|dreizehn|vierzehn|fünfzehn|fuenfzehn|sechzehn|siebzehn|achtzehn|'
    r'neunzehn|zwanzig)'
)
EXPERIENCE_UNIT = r'(?:years?|months?|jahr(?:e|en)?|monat(?:e|en)?)'
EXPERIENCE_CONTEXT = (
    r'(?:'
    r'(?:\s+(?:of|in|von)\s+)?(?:[\w\'-]+\s+){0,6}'
    r'(?:(?:work(?:ing)?\s+)?experience|(?:berufs|praxis)?erfahrung)\b'
    r'|\s+in\s+(?:a\s+)?(?:similar|comparable|related|relevant|equivalent)\s+'
    r'(?:role|position|field|industry)\b'
    r')'
)
EXPERIENCE_PATTERN = re.compile(
    rf'\b(?:(?P<range_min>{EXPERIENCE_NUMBER})\s*(?:to|bis|[-–—])\s*'
    rf'(?P<range_max>{EXPERIENCE_NUMBER})\s*(?P<range_unit>{EXPERIENCE_UNIT})\b'
    rf"(?:['’]s?)?(?P<range_context>{EXPERIENCE_CONTEXT})?|"
    rf'(?:(?P<marker>at\s+least|minimum(?:\s+of)?|min\.?|mindestens|mind\.?|ab)\s*)?'
    rf'(?P<minimum>{EXPERIENCE_NUMBER})\s*(?P<plus>\+|plus)?\s*'
    rf'(?P<minimum_unit>{EXPERIENCE_UNIT})\b(?:[\'’]s?)?(?P<minimum_context>{EXPERIENCE_CONTEXT})?|'
    rf'(?P<adjective>{EXPERIENCE_NUMBER})(?:\s*-\s*years?|\s*-?\s*jährig(?:e|en|er|es|em)?)'
    rf'(?P<adjective_context>{EXPERIENCE_CONTEXT})?|'
    rf'(?P<reverse_term>berufserfahrung|praxiserfahrung|work(?:ing)?\s+experience|experience|erfahrung)'
    rf'\s+(?:of|von)\s+(?P<reverse_marker>at\s+least|minimum(?:\s+of)?|min\.?|mindestens|mind\.?|ab)?\s*'
    rf'(?P<reverse>{EXPERIENCE_NUMBER})\s*(?P<reverse_unit>{EXPERIENCE_UNIT})\b(?:[\'’]s?)?)', re.I)
EXPERIENCE_NUMBER_WORDS = {
    'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11,
    'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
    'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20,
    'ein': 1, 'eine': 1, 'einen': 1, 'einem': 1, 'einer': 1, 'eins': 1,
    'zwei': 2, 'drei': 3, 'vier': 4, 'fünf': 5, 'fuenf': 5, 'sechs': 6,
    'sieben': 7, 'acht': 8, 'neun': 9, 'zehn': 10, 'elf': 11, 'zwölf': 12,
    'zwoelf': 12, 'dreizehn': 13, 'vierzehn': 14, 'fünfzehn': 15,
    'fuenfzehn': 15, 'sechzehn': 16, 'siebzehn': 17, 'achtzehn': 18,
    'neunzehn': 19, 'zwanzig': 20,
}
LANGUAGE_PATTERNS = {
    'German': re.compile(r'\b(?:german|deutsch(?:kenntnis(?:se)?)?)\b', re.I),
    'English': re.compile(r'\b(?:english|englisch(?:kenntnis(?:se)?)?)\b', re.I),
    'French': re.compile(r'\b(?:french|französisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Spanish': re.compile(r'\b(?:spanish|spanisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Italian': re.compile(r'\b(?:italian|italienisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Dutch': re.compile(r'\b(?:dutch|niederländisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Portuguese': re.compile(r'\b(?:portuguese|portugiesisch(?:kenntnis(?:se)?)?)\b', re.I),
    # English "polish" is also a common verb; keep the demonym capitalized.
    'Polish': re.compile(r'\b(?:Polish|Polnisch|polnisch(?:e[nrs]?|kenntnis(?:se)?)?)\b'),
    'Russian': re.compile(r'\b(?:russian|russisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Turkish': re.compile(r'\b(?:turkish|türkisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Arabic': re.compile(r'\b(?:arabic|arabisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Chinese': re.compile(r'\b(?:chinese|mandarin|cantonese|chinesisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Japanese': re.compile(r'\b(?:japanese|japanisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Korean': re.compile(r'\b(?:korean|koreanisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Ukrainian': re.compile(r'\b(?:ukrainian|ukrainisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Greek': re.compile(r'\b(?:greek|griechisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Czech': re.compile(r'\b(?:czech|tschechisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Swedish': re.compile(r'\b(?:swedish|schwedisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Danish': re.compile(r'\b(?:danish|dänisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Norwegian': re.compile(r'\b(?:norwegian|norwegisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Finnish': re.compile(r'\b(?:finnish|finnisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Romanian': re.compile(r'\b(?:romanian|rumänisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Hungarian': re.compile(r'\b(?:hungarian|ungarisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Hebrew': re.compile(r'\b(?:hebrew|hebräisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Hindi': re.compile(r'\b(?:hindi|hindisch(?:kenntnis(?:se)?)?)\b', re.I),
}
LANGUAGE_LEVEL = re.compile(r'\b(?:A1|A2|B1|B2|C1|C2|(?<!near[- ])native(?![- ](?:like|level))(?:\s+speaker)?|mother tongue|first language|muttersprache\w*|muttersprachlich\w*)\b', re.I)
LANGUAGE_FLUENCY = re.compile(
    r'\b(?:fluent|fluently|fluency|fließend\w*|fliessend\w*|flüssig\w*|fluessig\w*|'
    r'verhandlungssicher\w*|kommunikationssicher\w*|sprachgewandt\w*|stilsicher\w*|'
    r'souverän\w*|souveraen\w*|sprachlich\s+versiert|bilingual|professional\s+(?:working\s+)?proficiency|'
    r'native[- ]like|near[- ]native|native|mother tongue|'
    r'muttersprache\w*|muttersprachlich\w*|(?:very\s+)?good(?:\s+(?:command|knowledge|language skills?))?|'
    r'(?:sehr\s+)?gut\w*(?:\s+\w*)?kenntnis(?:se)?|'
    r'(?:ausgezeichnet|exzellent|hervorragend)\w*(?:\s+\w*)?kenntnis(?:se)?|'
    r'excellent|outstanding|expert|mastery|strong\s+(?:command|knowledge|language skills?)|'
    r'exceptional\s+(?:command|knowledge)|advanced|fortgeschritten\w*|conversational|'
    r'konversationssicher\w*|proficient|working\s+(?:knowledge|proficiency)|'
    r'solide\w*|sicher\w*|basic|grundkenntnis(?:se)?|elementary|beginner|very\s+good)\b', re.I)
LANGUAGE_QUALITATIVE_LEVELS = (
    (re.compile(r'\b(?:verhandlungssicher\w*|business[- ]fluent|near[- ]native|near native|native[- ]like|full professional proficiency|professional proficiency|professional working proficiency|bilingual proficiency|native[- ]level)\b', re.I), 'C1', 'C2'),
    (re.compile(r'\b(?:very\s+good|sehr\s+gut\w*|excellent|ausgezeichnet\w*|exzellent\w*|hervorragend\w*|fluent|fluently|fluency|fließend\w*|fliessend\w*|flüssig\w*|fluessig\w*|advanced|fortgeschritten\w*|proficient|bilingual|souverän\w*|souveraen\w*|kommunikationssicher\w*|sprachgewandt\w*|stilsicher\w*|sprachlich\s+versiert|outstanding|expert\s+(?:command|knowledge)|mastery|strong\s+(?:command|knowledge|language skills?)|exceptional\s+(?:command|knowledge))\b', re.I), 'C1', 'C2'),
    (re.compile(r'\b(?:good|gut\w*|conversational|konversationssicher\w*|working knowledge|working proficiency|intermediate|mittelstufe|solide\w*|sicher\w*)\b', re.I), 'B1', 'B2'),
    (re.compile(r'\b(?:basic|grundkenntnis(?:se)?|basiskenntnis(?:se)?|elementary|beginner|anfängerkenntnis(?:se)?)\b', re.I), 'A1', 'A2'),
)
LANGUAGE_APPLICANT_CUE = re.compile(
    r'\b(?:you\s+(?:speak|are fluent|have|possess)|sie\s+(?:sprechen|verfügen)|'
    r'du\s+(?:sprichst|verfügst)|your\s+language skills?|deine?\s+sprachkenntnisse|'
    r'ihre?\s+sprachkenntnisse|ability\s+to\s+(?:communicate|write|speak)|'
    r'(?:written|spoken|oral|verbal)\s+(?:language|communication)\s+skills?|'
    r'(?:language|communication)\s+skills?\s+(?:in|with)|'
    r'(?:schriftlich|mündlich)\w*\s+(?:kommunizieren|kommunikationsfähigkeit|sprachkenntnisse))\b', re.I)
LANGUAGE_WRITTEN = re.compile(r'\b(?:written|in writing|schriftlich\w*|schriftliche[nrms]?|schriftliches)\b', re.I)
LANGUAGE_SPOKEN = re.compile(r'\b(?:spoken|oral|verbal|verbally|mündlich\w*|mündliche[nrms]?|mündliches)\b', re.I)
LANGUAGE_MODALITY_PHRASE = re.compile(
    r'\b(?:(?:written|spoken|oral|verbal)\s*(?:and|&|/|as well as)\s*'
    r'(?:written|spoken|oral|verbal)|in\s+wort\s+und\s+schrift|'
    r'mündlich\w*\s+und\s+schriftlich\w*|schriftlich\w*\s+und\s+mündlich\w*)\b', re.I)
LANGUAGE_EXPECTED_FOLLOWING = re.compile(
    r'^\s*(?:'
    r'\(\s*(?:mind(?:estens)?|min(?:imum)?|at\s+least|A1|A2|B1|B2|C1|C2|'
    r'native(?:\s+speaker)?|mother\s+tongue|first\s+language|muttersprache\w*|muttersprachlich\w*)\b\.?|'
    r'[-,.;:!?/()\[\]{}](?!\w)|'
    r'(?:[- ]speaking\b(?!\s+(?:markets?|marketplace|märkte?|markt\w*)))|'
    r'(?:language|communication|skills?|proficiency|fluency|knowledge|command|ability|'
    r'written|spoken|oral|verbal|speaking|speaker|'
    r'schriftlich\w*|mündlich\w*|sprachlich\w*|kenntnis\w*|sprachniveau|'
    r'native|mother\s+tongue|first\s+language|'
    r'A1|A2|B1|B2|C1|C2|mindestens|minimum|at\s+least|'
    r'very|good|sehr|gut\w*|excellent|ausgezeichnet\w*|exzellent\w*|hervorragend\w*|'
    r'fluent|fluently|fließend\w*|fliessend\w*|flüssig\w*|fluessig\w*|'
    r'advanced|fortgeschritten\w*|proficient|bilingual|professional|full|business|near|native|'
    r'strong|exceptional|outstanding|expert|mastery|kommunikationssicher\w*|sprachgewandt\w*|'
    r'stilsicher\w*|souverän\w*|souveraen\w*|sprachlich\s+versiert|'
    r'conversational|konversationssicher\w*|'
    r'working|solide\w*|sicher\w*|basic|grundkenntnis\w*|elementary|beginner|'
    r'for|to|in|with|at|on|of|by|as|für|mit|zu|im|am|zur|zum|von|'
    r'and|or|und|oder|is|are|required|mandatory|essential|necessary|needed|'
    r'erforderlich|notwendig|pflicht|preferred|preferably|wünschenswert'
    r')\b)', re.I)


def _qualitative_language_level(text: str) -> tuple[str, list[str]] | None:
    """Return a conservative CEFR floor and the wording's estimated range."""
    for pattern, minimum, maximum in LANGUAGE_QUALITATIVE_LEVELS:
        if pattern.search(text):
            return minimum, [minimum, maximum]
    return None


def _language_modality(text: str) -> str | None:
    if LANGUAGE_MODALITY_PHRASE.search(text):
        return 'written_and_spoken'
    written = bool(LANGUAGE_WRITTEN.search(text))
    spoken = bool(LANGUAGE_SPOKEN.search(text))
    if written and spoken:
        return 'written_and_spoken'
    if written:
        return 'written'
    if spoken:
        return 'spoken'
    return None


def _merge_language_modalities(first: str | None, second: str | None) -> str | None:
    if first == 'written_and_spoken' or second == 'written_and_spoken':
        return 'written_and_spoken'
    modes = {mode for mode in (first, second) if mode}
    if modes == {'written', 'spoken'}:
        return 'written_and_spoken'
    return next(iter(modes), None)


def _language_level_label(language: dict) -> str:
    name = language['value']
    level_range = language.get('level_range')
    if language.get('level_inferred') and isinstance(level_range, list) and len(level_range) == 2:
        return f'{name} estimated {level_range[0]}–{level_range[1]}'
    level = language.get('level')
    return f'{name} {level}' if level else f'{name} fluency'


def _language_modifier_segments(before: str, after: str, *, has_next_language: bool) -> tuple[str, str]:
    """Keep modifiers on their side of a language list's conjunction boundary."""
    separator = r'\b(?:and|und|or|oder)\b|,'
    before = before[-70:]
    after = after[:70]
    if not LANGUAGE_MODALITY_PHRASE.search(before):
        before = re.split(separator, before, flags=re.I)[-1]
    if not LANGUAGE_MODALITY_PHRASE.search(after):
        after_separator = separator if has_next_language else r'\b(?:and|und|or|oder)\b'
        after = re.split(after_separator, after, maxsplit=1, flags=re.I)[0]
    return before, after


def _direct_language_requirement(before: str, after: str) -> bool:
    before_required = re.search(
        r'\b(?:require(?:s|d)?|need(?:s|ed)?|must(?:\s+have)?)\s*'
        r'(?:(?:written|spoken|oral|verbal|sprachlich\w*|schriftlich\w*|mündlich\w*)'
        r'(?:\s+(?:and|&|/)\s*(?:written|spoken|oral|verbal|sprachlich\w*|schriftlich\w*|mündlich\w*))?\s*)?$',
        before, re.I)
    after_language_target = re.match(
        r'\s*(?:language(?:\s+(?:skills?|proficiency|knowledge|fluency))?\b|'
        r'communication(?:\s+skills?)?\b|speakers?\b|'
        r'(?:skills?|proficiency|knowledge|fluency)\b|'
        r'(?:written|spoken|oral|verbal|sprachlich\w*|schriftlich\w*|mündlich\w*)\b|'
        r'in\s+(?:writing|wort\s+und\s+schrift)\b|'
        r'(?:A1|A2|B1|B2|C1|C2)\b|'
        r'(?:for|to)\s+(?:written|spoken|oral|verbal|communication|communicat\w*|'
        r'writing|speaking|work(?:ing)?\s+with)\b)', after, re.I)
    after_is_empty = bool(re.fullmatch(r'\s*[.!?,;:)]*\s*', after))
    before_required = bool(before_required and (after_is_empty or after_language_target))
    after_required = re.match(
        r'\s*(?:(?:language|communication|writing|speaking|speakers?|ability)\s+|'
        r'(?:written|spoken|oral|verbal|sprachlich\w*|schriftlich\w*|mündlich\w*)'
        r'(?:\s+(?:and|&|/)\s*(?:written|spoken|oral|verbal|sprachlich\w*|schriftlich\w*|mündlich\w*))?\s+|'
        r'(?:skills?|proficiency|knowledge|fluency)\s+|'
        r'in\s+(?:writing|wort\s+und\s+schrift)\s+)*'
        r'(?:(?:is|are)\s+)?(?:required|mandatory|essential|necessary|needed|pflicht|erforderlich|notwendig)\b',
        after, re.I)
    after_requirement_noun = re.match(r'\s*(?:is\s+)?a\s+requirement\b', after, re.I)
    return bool(before_required or after_required or after_requirement_noun)


def _bare_language_clause(clause: str, language_hits: list[tuple[str, re.Match]]) -> bool:
    remainder = clause
    for _, hit in reversed(language_hits):
        remainder = remainder[:hit.start()] + remainder[hit.end():]
    return not re.sub(r'[\s,.;:()\[\]{}\-–—&/]+', '', remainder)


def _ambiguous_language_mention(language: str, clause: str, found: re.Match) -> bool:
    if language == 'Polish' and re.match(
            r'\s+(?:up|off|out|your|the|a|an|my|our|their|this|that|these|those)\b',
            clause[found.end():], re.I):
        return True
    return False


def _language_modifier_evidence(text: str) -> bool:
    return bool(LANGUAGE_LEVEL.search(text) or LANGUAGE_FLUENCY.search(text) or
                LANGUAGE_APPLICANT_CUE.search(text) or LANGUAGE_WRITTEN.search(text) or
                LANGUAGE_SPOKEN.search(text) or LANGUAGE_MODALITY_PHRASE.search(text))


def _shared_language_requirement(clause: str, language_hits: list[tuple[str, re.Match]]) -> bool:
    """Recognize a requirement attached to a plain list of languages only."""
    if len(language_hits) < 2:
        return False
    for (_, left), (_, right) in zip(language_hits, language_hits[1:]):
        connector = clause[left.end():right.start()]
        if re.fullmatch(r'\s*(?:and|und|or|oder|,|/)\s*', connector, re.I):
            continue
        modified_list_item = re.match(r'\s*(?:and|und|or|oder|,|/)\s*(.+?)\s*$', connector, re.I)
        if not modified_list_item or not _language_modifier_evidence(modified_list_item.group(1)):
            return False
    after_last = clause[language_hits[-1][1].end():]
    return (_direct_language_requirement('', after_last) or
            _direct_language_requirement(clause[:language_hits[0][1].start()], after_last))


def plain_text(value) -> str:
    if not isinstance(value, str):
        return ''
    # Bound parser input while retaining late job content after large navigation blocks.
    chunks = [value] if len(value) <= 240000 else [value[:120000], value[-120000:]]
    parts = []
    for chunk in chunks:
        doc = Document()
        doc.feed(chunk)
        parts.extend(doc.text)
    result = ' '.join(' '.join(parts).split())
    return result if len(result) <= 240000 else result[:120000] + ' ' + result[-120000:]


@lru_cache(maxsize=4096)
def _pattern(term: str):
    return re.compile(r'(?<![\w+#])' + re.escape(term) + r'(?![\w+#])', re.I)


def _evidence(value, text, match, source):
    return {'value': value, 'source': source,
            'evidence': text[max(0, match.start()-50):min(len(text), match.end()+70)]}


def _mention_kind(text: str, match, source: str) -> str:
    if source == 'title':
        return 'title'
    cursor = 0
    sentence = text
    section = 'neutral'
    for clause, scope in scoped_sentences(text, initial_scope=structured_source_scope(source)):
        position = text.find(clause, cursor)
        if position < 0:
            continue
        cursor = position + len(clause)
        if position <= match.start() < cursor:
            sentence, section = clause, scope
            break
    if section == 'benefits' or other_actor(sentence):
        return 'incidental'
    if section == 'optional':
        return 'optional'
    if NON_REQUIREMENT.search(sentence):
        return 'incidental'
    if OPTIONAL.search(sentence):
        return 'optional'
    return 'mentioned'


def extract_profile_skills(text: str) -> list[str]:
    text = plain_text(text)
    return [name for name, aliases in SKILLS.items() if any(_pattern(alias).search(text) for alias in aliases)]


def normalize_skills(skills: list[str]) -> list[str]:
    result = []
    for skill in skills:
        name = next((name for name, aliases in SKILLS.items()
                     if skill.casefold() in [name.casefold(), *aliases]), skill.strip())
        if name and name not in result:
            result.append(name)
    return result


def _normalized(value: str, groups: dict[str, list[str]]) -> str | None:
    for name, aliases in groups.items():
        if any(_pattern(alias).search(value) for alias in aliases):
            return name
    return None


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r'(?<=[.!?])\s+|\s*[;\n]\s*', text) if part.strip()]


def _quantified_scripting_lists(text: str):
    return [match for match in QUANTIFIED_SCRIPTING_LIST.finditer(text)
            if re.search(r',|\b(?:and|und|or|oder)\b', match.group(1), re.I)]


def _requirement_clauses(sentence: str) -> list[str]:
    """Keep shared wording together, but separate independently qualified clauses."""
    # Keep this measured one-of list intact: its commas cannot create independent
    # requirements or detach an optional qualifier from the end of the clause.
    protected = {}
    for index, match in reversed(list(enumerate(_quantified_scripting_lists(sentence)))):
        marker = f'\ue000q{index}\ue001'
        protected[marker] = match.group(1)
        sentence = sentence[:match.start(1)] + marker + sentence[match.end(1):]
    for index, match in reversed(list(enumerate(LANGUAGE_MODALITY_PHRASE.finditer(sentence)))):
        marker = f'\ue000m{index}\ue001'
        protected[marker] = match.group()
        sentence = sentence[:match.start()] + marker + sentence[match.end():]

    def split_scoped(text: str, separator: str | None) -> list[str]:
        if separator is None:
            parts = []
            cursor = 0
            for match in re.finditer(r',\s*', text):
                following = text[match.end():]
                if re.match(r'(?:(?:both)\s+)?(?:written|spoken|oral|verbal|schriftlich|mündlich|in\s+wort\s+und\s+schrift)\b', following, re.I):
                    continue
                level = re.match(r'(?:A1|A2|B1|B2|C1|C2)\b', following, re.I)
                if level and not any(pattern.match(following[level.end():].lstrip())
                                     for pattern in LANGUAGE_PATTERNS.values()):
                    continue
                parts.append(text[cursor:match.start()].strip())
                cursor = match.end()
            parts.append(text[cursor:].strip())
            parts = [part for part in parts if part]
        else:
            parts = [part.strip() for part in re.split(separator, text, flags=re.I) if part.strip()]
        if not parts:
            return []

        def language_scoped(part: str) -> bool:
            return (any(pattern.search(part) for pattern in LANGUAGE_PATTERNS.values()) and
                    bool(LANGUAGE_LEVEL.search(part) or LANGUAGE_FLUENCY.search(part) or
                         LANGUAGE_APPLICANT_CUE.search(part) or
                         LANGUAGE_WRITTEN.search(part) or LANGUAGE_SPOKEN.search(part)))

        def has_language_requirement(part: str) -> bool:
            hits = sorted((match for pattern in LANGUAGE_PATTERNS.values()
                           if (match := pattern.search(part))), key=lambda match: match.start())
            return bool(hits and _direct_language_requirement(part[:hits[0].start()], part[hits[-1].end():]))

        # Group language-list members from their trailing qualifier before
        # an earlier independent scope can absorb an unqualified member.
        for index in range(len(parts) - 2, -1, -1):
            member = re.sub(r'\b(?:A1|A2|B1|B2|C1|C2)\b', '', parts[index], flags=re.I).strip()
            following = parts[index + 1]
            following_scoped = OPTIONAL.search(following) or REQUIREMENT.search(following) or NON_REQUIREMENT.search(following)
            if (any(pattern.fullmatch(member) for pattern in LANGUAGE_PATTERNS.values()) and
                    following_scoped and any(pattern.search(following) for pattern in LANGUAGE_PATTERNS.values())):
                parts[index:index + 2] = [parts[index] + ' and ' + following]
        # Group a qualified language list from its final shared requirement
        # suffix before the left-to-right scope pass can detach early items.
        if (has_language_requirement(parts[-1]) and
                not (OPTIONAL.search(parts[-1]) or NON_REQUIREMENT.search(parts[-1]))):
            chain_start = len(parts) - 1
            while chain_start > 0:
                previous = parts[chain_start - 1]
                current = parts[chain_start]
                if (not language_scoped(previous) or not language_scoped(current) or
                        OPTIONAL.search(previous) or NON_REQUIREMENT.search(previous) or
                        OPTIONAL.search(current) or NON_REQUIREMENT.search(current)):
                    break
                chain_start -= 1
            if chain_start < len(parts) - 1:
                parts[chain_start:] = [' and '.join(parts[chain_start:])]

        clauses = []
        current = parts[0]
        for part in parts[1:]:
            current_scoped = OPTIONAL.search(current) or REQUIREMENT.search(current) or NON_REQUIREMENT.search(current)
            part_scoped = OPTIONAL.search(part) or REQUIREMENT.search(part) or NON_REQUIREMENT.search(part)
            current_language_scoped = language_scoped(current)
            part_language_scoped = language_scoped(part)
            current_language_match = min(
                (match for pattern in LANGUAGE_PATTERNS.values()
                 if (match := pattern.search(current))),
                key=lambda match: match.start(), default=None)
            leading_language_requirement = bool(current_language_match and re.search(
                r'\b(?:require(?:s|d)?|need(?:s|ed)?|must(?:\s+have)?)\b',
                current[:current_language_match.start()], re.I))
            separately_scoped = ((current_scoped and part_scoped) or
                                 (current_language_scoped and part_language_scoped) or
                                 (leading_language_requirement and
                                  bool(OPTIONAL.search(part) or NON_REQUIREMENT.search(part))))
            preserve_leading_language_list = (leading_language_requirement and
                                              current_language_scoped and part_language_scoped and
                                              not (OPTIONAL.search(part) or NON_REQUIREMENT.search(part)))
            preserve_shared_language_requirement = (current_language_scoped and part_language_scoped and
                                                    has_language_requirement(part) and
                                                    not (OPTIONAL.search(current) or NON_REQUIREMENT.search(current)) and
                                                    not (OPTIONAL.search(part) or NON_REQUIREMENT.search(part)))
            if separately_scoped and not (preserve_leading_language_list or
                                           preserve_shared_language_requirement):
                clauses.append(current)
                current = part
            else:
                current += ' and ' + part
        clauses.append(current)
        return clauses

    # Resolve clause boundaries before splitting coordinated lists: their trailing
    # qualifier must stay with the whole list when deciding the outer scope.
    barriers = re.split(r'\b(?:but|aber|jedoch)\b|\s+[-–]\s+(?=(?:gute|good|fluent|fließende)\s+(?:englisch|english|deutsch|german))', sentence, flags=re.I)
    clauses = [clause for barrier in barriers for clause in split_scoped(barrier, None)]
    parts = [part for clause in clauses for part in split_scoped(clause, r'\b(?:and|und)\b')]
    for marker, original in protected.items():
        parts = [part.replace(marker, original) for part in parts]
    return parts


def _required_context(sentence: str, *, explicit: bool = False, duties: bool = False, start=None, end=None) -> bool:
    polarity = requirement_polarity(sentence, start, end) if start is not None else None
    if is_source_instruction(sentence) or (polarity is not None and polarity != 'required'):
        return False
    if (polarity is None and OPTIONAL.search(sentence)) or NON_REQUIREMENT.search(sentence):
        return False
    if duties:
        # Requirements can be a deliverable ("gather requirements"), while
        # explicit applicant cues such as "Python required" still apply here.
        sentence = re.sub(
            r'\b(?:gather(?:ing)?|collect(?:ing)?|analy[sz](?:e|ing)|document(?:ing)?|'
            r'manag(?:e|ing)|defin(?:e|ing)|erheben|erfassen|sammeln|analysieren|dokumentieren)\s+'
            r'(?:(?:business|technical|customer|functional|user|software|fachliche|technische)\s+){0,3}'
            r'(?:requirements?|anforderungen)\b|'
            r'\b(?:requirements?|anforderungen)\s+(?:gathering|collection|analysis|management|'
            r'erheben|erfassen|sammeln|analysieren|dokumentieren)\b', '', sentence, flags=re.I)
    return explicit or bool(REQUIREMENT.search(sentence))


def _experience_years(match) -> int | float | None:
    number = None
    unit = None
    for number_key, unit_key in (('range_min', 'range_unit'), ('minimum', 'minimum_unit'),
                                 ('adjective', None), ('reverse', 'reverse_unit')):
        token = match.group(number_key)
        if token:
            normalized = token.casefold().replace(',', '.')
            try:
                number = float(normalized)
            except ValueError:
                number = EXPERIENCE_NUMBER_WORDS.get(normalized)
            unit = match.group(unit_key) if unit_key else 'years'
            break
    if number is None:
        return None
    if unit and unit.casefold().startswith(('month', 'monat')):
        number /= 12
    return int(number) if float(number).is_integer() else round(number, 2)


def _is_experience_match(match) -> bool:
    """Ignore bare durations unless the wording explicitly signals experience."""
    context = any(match.group(name) for name in
                  ('range_context', 'minimum_context', 'adjective_context', 'reverse_term'))
    explicit_minimum = any(match.group(name) for name in ('marker', 'plus', 'reverse_marker'))
    return bool(context or explicit_minimum)


def _job_requirements(texts: list[tuple[str, str]]) -> tuple[dict | None, list[dict], list[dict], list[dict]]:
    """Extract stated requirements from the sentence containing the evidence."""
    experience = None
    languages = []
    qualifications = []
    deferred = []
    seen_qualifications = set()
    for source, text in texts:
        if source == 'title':
            continue
        for sentence, scope in scoped_sentences(text, initial_scope=structured_source_scope(source)):
            requirement_section = scope == 'requirements'
            if scope == 'optional':
                continue
            clauses = _requirement_clauses(sentence)
            if requirement_section and DEGREE.search(sentence) and ',' in sentence:
                base, remainder = sentence.split(',', 1)
                if DEGREE.search(base) and OPTIONAL.search(remainder):
                    clauses = _requirement_clauses(base) + _requirement_clauses(remainder)
            for clause in clauses:
                experience_matches = [exp for exp in EXPERIENCE_PATTERN.finditer(clause)
                                      if _is_experience_match(exp)]
                for exp in experience_matches:
                    minimum = bool(exp.group('range_max') or exp.group('marker') or
                                   exp.group('reverse_marker') or exp.group('plus'))
                    if experience is None and _required_context(clause, explicit=requirement_section or (scope == 'neutral' and minimum), duties=scope == 'duties', start=exp.start(), end=exp.end()):
                        years = _experience_years(exp)
                        if years is not None:
                            experience = _evidence(years, clause, exp, source)
                language_hits = sorted(((name, hit) for name, pattern in LANGUAGE_PATTERNS.items()
                                        if (hit := pattern.search(clause))), key=lambda pair: pair[1].start())
                level_info = []
                non_language_tail_info = []
                for index, (language, found) in enumerate(language_hits):
                    next_start = language_hits[index + 1][1].start() if index + 1 < len(language_hits) else len(clause)
                    previous_end = language_hits[index - 1][1].end() if index else 0
                    after = clause[found.end():next_start]
                    non_language_tail = bool(after.strip() and
                                             not LANGUAGE_EXPECTED_FOLLOWING.match(after))
                    non_language_tail_info.append(non_language_tail)
                    before = clause[previous_end:found.start()]
                    modifier_before, modifier_after = _language_modifier_segments(
                        before, after, has_next_language=index + 1 < len(language_hits))
                    level = None if non_language_tail else LANGUAGE_LEVEL.search(after[:40])
                    if level and re.search(r'\b(?:and|und|or|oder)\b', after[:level.start()], re.I):
                        level = None
                    if not level and not non_language_tail:
                        level = re.search(r'\b(?:A1|A2|B1|B2|C1|C2)\b\s*$', before[-15:], re.I)
                    if not level and not non_language_tail:
                        level = re.search(r'\b(?:native(?:\s+speaker)?|mother tongue|first language|muttersprache\w*|muttersprachlich\w*)\s*(?:of|in)?\s*$', before[-40:], re.I)
                    level_value = level.group().strip().upper() if level else None
                    level_value = ('NATIVE' if level_value and level_value != 'NATIVE' and
                                   not re.fullmatch(r'A1|A2|B1|B2|C1|C2', level_value) else level_value)
                    info = {'level': level_value, 'inferred': False, 'range': None} if level_value else None
                    if not info:
                        # Remove the language token while keeping its localized
                        # modifiers adjacent, so phrases like “strong English
                        # language skills” retain the “strong language skills” cue.
                        nearby = f'{modifier_before} {modifier_after}'
                        estimate = None if non_language_tail else _qualitative_language_level(nearby)
                        if estimate:
                            info = {'level': estimate[0], 'inferred': True, 'range': estimate[1]}
                    level_info.append(info)
                modality_info = []
                for index, (language, found) in enumerate(language_hits):
                    next_start = language_hits[index + 1][1].start() if index + 1 < len(language_hits) else len(clause)
                    previous_end = language_hits[index - 1][1].end() if index else 0
                    modifier_before, modifier_after = _language_modifier_segments(
                        clause[previous_end:found.start()], clause[found.end():next_start],
                        has_next_language=index + 1 < len(language_hits))
                    modality_info.append(_language_modality(
                        modifier_before + clause[found.start():found.end()] + modifier_after))
                for index in range(len(language_hits) - 2, -1, -1):
                    connector = clause[language_hits[index][1].end():language_hits[index + 1][1].start()]
                    if not level_info[index] and level_info[index + 1] and re.fullmatch(r'\s*(?:and|und|or|oder|,)\s*', connector, re.I):
                        level_info[index] = dict(level_info[index + 1])
                for index in range(len(language_hits) - 1):
                    connector = clause[language_hits[index][1].end():language_hits[index + 1][1].start()]
                    if level_info[index] and not level_info[index + 1] and re.fullmatch(r'\s*(?:and|und|,)\s*', connector, re.I):
                        level_info[index + 1] = dict(level_info[index])
                    if modality_info[index] and not modality_info[index + 1] and re.fullmatch(r'\s*(?:and|und|,)\s*', connector, re.I):
                        modality_info[index + 1] = modality_info[index]
                for index in range(len(language_hits) - 2, -1, -1):
                    connector = clause[language_hits[index][1].end():language_hits[index + 1][1].start()]
                    if modality_info[index + 1] and not modality_info[index] and re.fullmatch(r'\s*(?:and|und|,)\s*', connector, re.I):
                        modality_info[index] = modality_info[index + 1]
                alternative_groups = {}
                for index in range(len(language_hits) - 1):
                    connector = clause[language_hits[index][1].end():language_hits[index + 1][1].start()]
                    if re.search(r'\b(?:or|oder)\b', connector, re.I) and not re.search(r'\b(?:and|und)\b', connector, re.I):
                        group = alternative_groups.get(index) or hashlib.sha256(
                            f'{source}:{clause}:{index}'.encode()).hexdigest()[:16]
                        alternative_groups[index] = alternative_groups[index + 1] = group
                for index, (language, found) in enumerate(language_hits):
                    info = level_info[index]
                    level = info['level'] if info else None
                    if _ambiguous_language_mention(language, clause, found):
                        continue
                    if scope == 'benefits' and not REQUIREMENT.search(clause):
                        continue
                    next_start = language_hits[index + 1][1].start() if index + 1 < len(language_hits) else len(clause)
                    previous_end = language_hits[index - 1][1].end() if index else 0
                    modifier_before, modifier_after = _language_modifier_segments(
                        clause[previous_end:found.start()], clause[found.end():next_start],
                        has_next_language=index + 1 < len(language_hits))
                    language_context = modifier_before + clause[found.start():found.end()] + modifier_after
                    applicant_language = (not non_language_tail_info[index] and
                                          bool(LANGUAGE_APPLICANT_CUE.search(language_context)))
                    language_fluency = (not non_language_tail_info[index] and
                                        bool(LANGUAGE_FLUENCY.search(language_context)))
                    modality_requirement = requirement_section and bool(_language_modality(language_context))
                    direct_language_requirement = _direct_language_requirement(modifier_before, modifier_after)
                    if (index + 1 < len(language_hits) and
                            re.fullmatch(r'\s*[.!?,;:)]*\s*', modifier_after)):
                        # A requirement verb before the first language cannot
                        # borrow the empty segment created by the next list item.
                        direct_language_requirement = _direct_language_requirement('', modifier_after)
                    language_signal = bool(level or language_fluency or applicant_language or
                                           modality_requirement or
                                           direct_language_requirement or
                                           _shared_language_requirement(clause, language_hits) or
                                           (requirement_section and _bare_language_clause(clause, language_hits)))
                    if not language_signal or not _required_context(
                            clause, explicit=requirement_section or (scope == 'neutral' and language_signal), duties=scope == 'duties', start=found.start(), end=found.end()):
                        continue
                    group = alternative_groups.get(index)
                    previous = next((row for row in languages if row['value'] == language
                                     and row.get('alternative_group') == group), None)
                    if previous:
                        previous_rank = CEFR_RANK.get(previous.get('level'), 0)
                        current_rank = CEFR_RANK.get(level, 0)
                        explicit_upgrade = (previous_rank == current_rank and previous.get('level_inferred') and
                                            not (info and info['inferred']))
                        if previous_rank > current_rank or (previous_rank == current_rank and not explicit_upgrade):
                            merged_modality = _merge_language_modalities(
                                previous.get('modality'), modality_info[index])
                            if merged_modality:
                                previous['modality'] = merged_modality
                            continue
                    previous_modality = previous.get('modality') if previous else None
                    if previous:
                        languages.remove(previous)
                    row = _evidence(language, clause, found, source)
                    row['level'] = level
                    if info and info['inferred']:
                        row['level_range'] = info['range']
                        row['level_inferred'] = True
                        row['kind'] = 'inferred_level'
                    else:
                        row['kind'] = ('explicit_cefr' if level and level != 'NATIVE' else
                                       'fluency' if language_fluency else 'language_requirement')
                    modality = modality_info[index]
                    merged_modality = _merge_language_modalities(previous_modality, modality)
                    if merged_modality:
                        row['modality'] = merged_modality
                    if group:
                        row['alternative_group'] = group
                    languages.append(row)
                if _required_context(clause, explicit=requirement_section or (scope == 'neutral' and bool(APPLICANT_CUE.search(clause))) or bool(re.search(r'\bSprachniveau\s+C[12]\b', clause, re.I)), duties=scope == 'duties'):
                    rows = qualification_rows(clause, source)
                    professional = bool(TENURE.search(clause))
                    alternative_lists = _quantified_scripting_lists(clause)
                    alternative_spans = [match.span(1) for match in alternative_lists]
                    for match in alternative_lists:
                        if scope in {'neutral', 'requirements'} and not any(
                                row['value'].casefold() == match.group(1).casefold() for row in deferred):
                            deferred.append({**_evidence(match.group(1), clause, match, source),
                                             'kind': 'scripting_alternative'})
                    for name, aliases in SKILLS.items():
                        # General skill evidence still uses the original text. Defer
                        # hard list-member interpretation when some alternatives
                        # (such as bash/Perl) are outside our skill vocabulary.
                        found = next((hit for alias in aliases for hit in _pattern(alias).finditer(clause)
                                      if not any(start <= hit.start() and hit.end() <= end
                                                 for start, end in alternative_spans)), None)
                        if found and (professional or not experience_matches):
                            rows.append({**_evidence(name, clause, found, source),
                                         'kind': 'professional_experience' if professional else 'skill'})
                    for row in rows:
                        key = json.dumps({name: (str(value).casefold() if name == 'value' else value)
                                          for name, value in row.items() if name not in {'source', 'evidence'}},
                                         sort_keys=True)
                        if key not in seen_qualifications:
                            qualifications.append(row)
                            seen_qualifications.add(key)
    return experience, languages, qualifications, deferred


def enrich_job(job: dict) -> dict:
    raw = job.get('raw_metadata') or {}
    source_hash = hashlib.sha256(json.dumps(job, sort_keys=True, default=str).encode()).hexdigest()
    texts = [('title', plain_text(job.get('title'))), ('description', structured_text(job.get('description')))]
    detail_requirements = raw.get('detail_requirements') or {}
    for prefix, values in [('structured', raw), ('detail', detail_requirements)]:
        for field in ('skills', 'qualifications', 'experienceRequirements', 'educationRequirements'):
            value = values.get(field)
            if value:
                texts.extend(structured_requirement_texts({field: value}, prefix))
    skills = []
    for name, aliases in SKILLS.items():
        evidence = None
        best_rank = -1
        for source, text in texts:
            for alias in aliases:
                for match in _pattern(alias).finditer(text):
                    kind = _mention_kind(text, match, source)
                    rank = {'incidental': 0, 'title': 1, 'optional': 2, 'mentioned': 3}[kind]
                    if rank > best_rank:
                        evidence = {**_evidence(name, text, match, source), 'name': name, 'kind': kind}
                        best_rank = rank
                    if best_rank == 3:
                        break
                if best_rank == 3:
                    break
            if best_rank == 3:
                break
        if evidence:
            skills.append(evidence)
    seniority = None
    levels = {'student': ['werkstudent', 'working student', 'intern', 'praktikum', 'ausbildung', 'apprentice'],
              'junior': ['junior', 'entry level', 'berufseinsteiger'],
              'lead': ['lead', 'principal', 'head of', 'teamleiter'], 'senior': ['senior']}
    level_text = plain_text(job.get('seniority')) or texts[0][1]
    seniority_levels = [name for name, aliases in levels.items() if any(_pattern(alias).search(level_text) for alias in aliases)]
    level = seniority_levels[0] if seniority_levels else None
    if level:
        seniority = {'value': level, 'source': 'seniority' if job.get('seniority') else 'title', 'evidence': level_text[:180]}
    experience, languages, requirements, deferred = _job_requirements(texts)
    for domain in DOMAIN.finditer(texts[0][1]):
        if not any(_pattern(domain.group()).search(row['evidence']) for row in requirements):
            requirements.append({'kind': 'title_domain', 'value': domain.group(),
                                 'source': 'title', 'evidence': texts[0][1]})
    if _pattern('werkstudent').search(texts[0][1]) or _pattern('working student').search(texts[0][1]):
        if not any(row['kind'] == 'enrolment' for row in requirements):
            requirements.append({'kind': 'enrolment', 'value': 'Current student enrolment',
                                 'source': 'title', 'evidence': texts[0][1]})
    structured_exp = detail_requirements.get('experienceRequirements') or raw.get('experienceRequirements')
    if isinstance(structured_exp, dict):
        try:
            months = float(structured_exp.get('monthsOfExperience'))
            if 0 <= months <= 600:
                experience = {'value': months / 12, 'source': 'detail.experienceRequirements' if detail_requirements.get('experienceRequirements') else 'structured.experienceRequirements', 'evidence': json.dumps(structured_exp)}
        except (ValueError, TypeError):
            pass
    description = texts[1][1]
    employment_groups = {'working_student': ['working student', 'werkstudent'], 'apprenticeship': ['apprentice', 'ausbildung'],
        'dual_study': ['dual_study', 'dual study', 'duales studium'],
        'internship': ['internship', 'intern', 'praktikum'], 'part_time': ['part_time', 'part-time', 'part time', 'teilzeit'],
        'full_time': ['full_time', 'full-time', 'full time', 'vollzeit'], 'contract': ['contract', 'freelance', 'freiberuflich']}
    employment_text = ' '.join(str(job.get(field) or '') for field in ('employment_type', 'schedule', 'title'))
    employment_types = [name for name, aliases in employment_groups.items() if any(_pattern(alias).search(employment_text) for alias in aliases)]
    employment = employment_types[0] if employment_types else None
    arrangement = job.get('work_arrangement') or ('remote' if job.get('is_remote') else None)
    warnings = []
    if len(description) < 350:
        warnings.append('Full description not available' if not description else 'Only a short description is available')
    if not skills:
        warnings.append('No skills recognized in the current vocabulary')
    if not seniority:
        warnings.append('Experience level not stated')
    if not arrangement:
        warnings.append('Work style not stated')
    if not job.get('salary'):
        warnings.append('Salary not stated')
    return {'version': VERSION, 'source_hash': source_hash, 'skills': skills, 'seniority': seniority,
            'seniority_levels': seniority_levels,
            'experience_years': experience, 'languages': languages, 'requirements': requirements,
            'deferred_requirements': deferred,
            'employment_type': employment,
            'employment_types': employment_types,
            'work_style': arrangement, 'description_chars': len(description),
            'quality': 'limited' if len(description) < 350 else 'description_available', 'warnings': warnings}


@lru_cache(maxsize=1024)
def _role_group_indexes(role: str) -> tuple[int, ...]:
    return tuple(index for index, group in enumerate(ROLE_GROUPS)
                 if any(_pattern(alias).search(role) for alias in group))


def _role_match(role: str, title: str) -> bool:
    role = role.strip().casefold()
    specific_roles = {'backend': ['backend','back-end','back end','full stack','fullstack'],
                      'frontend': ['frontend','front-end','front end','full stack','fullstack']}
    for focus, aliases in specific_roles.items():
        if _pattern(focus).search(role):
            return any(_pattern(alias).search(title) for alias in aliases)
    if _pattern(role).search(title):
        return True
    for index in _role_group_indexes(role):
        group = ROLE_GROUPS[index]
        if 'weiterbildung' in group and re.search(r'\b(?:facharzt|arzt|ärztin|physician)\b', title, re.I):
            continue
        if any(_pattern(alias).search(title) for alias in group):
            return True
    return False


# Common tools and general service skills alone say little about the work.
_GENERAL_SKILLS = {'Excel', 'Git', 'Customer service', 'Onboarding', 'Trello', 'Kanban'}
_UNSUPPORTED_PROFILE_CLAIM = re.compile(
    r'\b(?:no|not|never|without|lack\w*|kein\w*|nicht|ohne|want\w*|wish\w*|'
    r'aspir\w*|hope\w*|would like|interested in|learning|lernen|möchte\w*)\b', re.I)
_ACADEMIC_PROFILE_CLAIM = re.compile(r'\b(?:academic|research|university|course\w*|studium|universität|hochschule)\b', re.I)


def profile_content_sources(profile: dict) -> tuple[tuple[str, str], ...]:
    """Project only career facts, so contacts and drafting preferences cannot affect ranking."""
    sources = [('summary', profile.get('summary') or '')]
    for index, entry in enumerate((profile.get('application_details') or {}).get('experiences') or []):
        sources.append((f'experience.{index}', entry.get('description') or ''))
    sources.append(('career_evidence', (profile.get('application_personalization') or {}).get('career_evidence') or ''))
    cv_facts = profile.get('cv_career_facts') or {}
    for kind in ('experiences', 'education'):
        for index, entry in enumerate((cv_facts.get(kind) or [])[:40]):
            text = entry.get('description') or ''
            if isinstance(text, str):
                sources.append((f'cv_career_facts.{kind}[{index}].description', text[:10000]))
    for name, evidence in (profile.get('skill_evidence') or {}).items():
        note = evidence.get('note') if isinstance(evidence, dict) else None
        if isinstance(note, str) and note.strip():
            sources.append((f'skill_evidence.{name}.note', note))
    # Saved profiles pass this compact projection instead of personal details.
    sources.extend(tuple(row) for row in profile.get('content_sources') or [])
    return tuple(dict.fromkeys((source, text) for source, text in sources if text))


@lru_cache(maxsize=256)
def _profile_content_skills(sources: tuple[tuple[str, str], ...]) -> tuple[tuple[str, float, str], ...]:
    """Bounded, local vocabulary extraction; narrative never verifies qualifications."""
    found = {}
    for source, text in sources:
        for sentence, _ in scoped_sentences(structured_text(text)):
            if _UNSUPPORTED_PROFILE_CLAIM.search(sentence):
                continue
            weight = .5 if (source.startswith('cv_career_facts.education[') or
                             _ACADEMIC_PROFILE_CLAIM.search(sentence)) else 1.0
            for skill in extract_profile_skills(sentence):
                if weight > found.get(skill, (0, ''))[0]:
                    found[skill] = (weight, source)
    return tuple((skill, weight, source) for skill, (weight, source) in found.items())


def _content_overlap(job_evidence: dict, profile_skills: list[str], evidence_by_skill: dict,
                     narrative: tuple[tuple[str, float, str], ...]) -> dict:
    capabilities = {}
    for skill, weight, source in narrative:
        name = _content_skill_name(skill)
        if weight > capabilities.get(name, (0, ''))[0]:
            capabilities[name] = (weight, source)
    for skill in profile_skills:
        context = str((evidence_by_skill.get(skill.casefold()) or {}).get('context', '')).casefold()
        strength = .5 if context and context != 'professional' else 1.0
        name = _content_skill_name(skill)
        if strength >= capabilities.get(name, (0, ''))[0]:
            capabilities[name] = (strength, 'skills')
    totals, overlaps, specifics = {}, {}, {}
    matches = []
    for skill, row in job_evidence.items():
        name = _content_skill_name(skill)
        kind = row.get('kind', 'mentioned')
        weight = {'mentioned': 1.0, 'optional': .25}.get(kind, 0)
        totals[name] = max(totals.get(name, 0), weight)
        if not weight or name not in capabilities:
            continue
        strength, source = capabilities[name]
        overlaps[name] = max(overlaps.get(name, 0), weight * strength)
        if kind == 'mentioned' and skill not in _GENERAL_SKILLS:
            specifics[name] = max(specifics.get(name, 0), strength)
        matches.append({'skill': skill, 'profile_source': source, 'weight': strength,
                        'source': row.get('source'), 'evidence': row.get('evidence'), 'kind': kind})
    total, overlap, specific = sum(totals.values()), sum(overlaps.values()), sum(specifics.values())
    # A single substantive competency can surface a possible lead. Strong fit
    # requires at least two full-strength competencies, independent of title.
    score = min(85 * overlap / max(1, total), 85 if specific >= 2 else 59) if specific >= 1 else 0
    return {'score': round(score), 'coverage': round(overlap / max(1, total), 3), 'matches': matches}


@lru_cache(maxsize=512)
def _content_skill_name(skill: str) -> str:
    # Some vocabulary entries share aliases (project coordination/management).
    # One concept cannot provide two independent signals of fit.
    return normalize_skills([skill])[0]


def _condition_waived(text, hit):
    before = re.split(r'[,;]|\b(?:but|however|aber|jedoch)\b', text[:hit.start()], flags=re.I)[-1]
    after = re.split(r'[,;]|\b(?:but|however|aber|jedoch)\b', text[hit.end():], flags=re.I)[0]
    return bool(re.search(r'\b(?:no|kein\w*)\s+(?:[\w-]+\s+){0,4}$', before, re.I) or
                re.match(r'\s+(?:(?:is|are|ist|sind)\s+)?(?:not (?:required|necessary|mandatory|essential)|nicht (?:erforderlich|notwendig|zwingend))\b', after, re.I))


def _credential_evidence(text, hit, certifications):
    # Only explicit, current, same-scope saved credentials clear a license gate.
    # A career aspiration or employment near licensed colleagues cannot do so.
    phrase = text[:hit.end()].split(':')[-1]
    suffix = re.match(r'\s+in\s+(.+?)(?=\s+(?:is |are )?(?:required|mandatory)|[.;]|$)', text[hit.end():], re.I)
    if suffix:
        phrase += ' ' + suffix.group(1)
    stop = {'a', 'an', 'the', 'is', 'are', 'required', 'mandatory', 'must', 'hold',
            'have', 'possess', 'applicant', 'you', 'your', 'qualifications', 'in'}

    def words(value):
        return {'current' if word in {'active', 'current', 'valid', 'gültig'} else word
                for word in re.findall(r'\w+', value.casefold()) if word not in stop}

    required = words(phrase)
    for index, credential in enumerate(certifications):
        if (not re.search(r'\b(?:active|current|valid|gültig)\b', credential, re.I) or
                re.search(r'\bexpired|\binactive|\blapsed|\brevoked|\babgelaufen|'
                          r'\b(?:no longer|not|never|formerly|previously|nicht mehr|nicht)\s+(?:[\w-]+\s+){0,2}(?:active|current|valid|gültig)\b', credential, re.I)):
            continue
        if required and required <= words(credential):
            return f'profile.certifications[{index}]'
    return None


_LICENSE_CONDITION = re.compile(r'\b(?:Approbation|Berufszulassung|Staplerschein|Fahrerlaubnis)\b|\bErlaubnis\s+nach\s+§\s*\d+[a-z]?\b|\b(?:professional|pharmacist|medical|active|current)\s+(?:license|licence|registration)\b', re.I)
_AUTHORIZATION_CONDITION = re.compile(r'work (?:authorization|authorisation|permits?|rights?)|rights? to work|arbeitserlaubnis|arbeitsberechtigung', re.I)


def _eligibility_conditions(text, preferences, certifications, mandatory):
    if not mandatory or not (_LICENSE_CONDITION.search(text) or _AUTHORIZATION_CONDITION.search(text)):
        return []
    conditions = []
    parts = re.split(r'\s*(?:,|\b(?:and|und|but|aber|or|oder)\b)\s*', text, flags=re.I)
    for part in parts:
        if OPTIONAL.search(part):
            continue
        license_hit = _LICENSE_CONDITION.search(part)
        authorization_hit = _AUTHORIZATION_CONDITION.search(part)
        recognized = False
        if license_hit and requirement_polarity(part, license_hit.start(), license_hit.end()) == 'required' and not _condition_waived(part, license_hit):
            recognized = True
            source = _credential_evidence(part, license_hit, certifications)
            conditions.append({'kind': 'license', 'status': 'pass' if source else 'unknown',
                'reason': 'Current same-scope saved credential: ' + source if source else 'Required active professional credential is unverified: ' + part})
        if authorization_hit and requirement_polarity(part, authorization_hit.start(), authorization_hit.end()) == 'required' and not _condition_waived(part, authorization_hit):
            recognized = True
            country = str(preferences.get('authorization_country') or '').strip()
            same_scope = len(country) > 2 and bool(_pattern(country).search(part))
            authorized = same_scope and preferences.get('authorized_to_work') is True and preferences.get('sponsorship_required') is False
            denied = same_scope and preferences.get('authorized_to_work') is False
            conditions.append({'kind': 'authorization', 'status': 'pass' if authorized else 'fail' if denied else 'unknown',
                'reason': ('Saved authorization names the required jurisdiction.' if authorized else
                           'Saved facts explicitly deny authorization in the required jurisdiction.' if denied else
                           'Required jurisdiction-specific work authorization is unverified: ' + part)})
        # A shared noun or complex alternative cannot silently disappear. Keep
        # unresolved short scope fragments unknown instead of verifying the list.
        if not recognized and not license_hit and not authorization_hit and len(parts) > 1 and len(part.split()) <= 4 and not extract_profile_skills(part):
            conditions.append({'kind': 'scope', 'status': 'unknown', 'reason': 'Verify the scope of the compound eligibility condition: ' + part})
    if re.search(r'\b(?:or|oder)\b', text, re.I) and conditions:
        mixed = bool(re.search(r'\b(?:and|und)\b', text, re.I))
        status = ('unknown' if mixed else 'pass' if any(row['status'] == 'pass' for row in conditions) else
                  'unknown' if any(row['status'] == 'unknown' for row in conditions) else 'fail')
        return [{'kind': 'alternative', 'status': status,
                 'reason': 'Eligibility alternative: ' + ' '.join(row['reason'] for row in conditions if row['status'] == status)[:450]}]
    return conditions


def _skill_context(profile, skill):
    for name, entry in (profile.get('skill_evidence') or {}).items():
        if normalize_skills([name]) != normalize_skills([skill]):
            continue
        raw = str(entry.get('context') or '').strip().casefold()
        context = raw if raw in {'professional', 'academic', 'research'} else 'unspecified'
        field = 'note' if entry.get('note') else 'context'
        return {'context': context, 'scope_verified': context != 'unspecified',
                'source': f'profile.skill_evidence.{name}.{field}',
                'quote': str(entry.get(field) or '')[:500]}
    return {'context': 'declared', 'scope_verified': False, 'source': 'profile.skills', 'quote': skill}


def _scoped_experience_rows(text, total_years):
    pattern = (rf'\b(?P<number>{EXPERIENCE_NUMBER})\s+(?P<unit>{EXPERIENCE_UNIT})\b'
               rf'(?P<scope>.*?)(?=\s+(?:and|und)\s+{EXPERIENCE_NUMBER}\s+{EXPERIENCE_UNIT}\b|[.;\n]|$)')
    rows = []
    for hit in re.finditer(pattern, text, re.I):
        if requirement_polarity(text, hit.start(), hit.end()) != 'required':
            continue
        token = hit['number'].casefold().replace(',', '.')
        number = float(token) if re.fullmatch(r'\d+(?:\.\d+)?', token) else EXPERIENCE_NUMBER_WORDS.get(token)
        if number is None:
            continue
        years = number / 12 if hit['unit'].casefold().startswith(('month', 'monat')) else number
        contradicted = total_years is not None and total_years < years
        rows.append({'kind': 'experience', 'requirement': hit.group().strip(),
                     'number': number, 'unit': 'months' if hit['unit'].casefold().startswith(('month', 'monat')) else 'years',
                     'minimum_years': round(years, 2), 'scope': re.sub(r'^\s*(?:of|in|von)\s+', '', hit['scope'], flags=re.I).strip(),
                     'modality': 'required', 'status': 'missing' if contradicted else 'unknown',
                     'job_source': 'description', 'job_quote': hit.group().strip(),
                     'evidence_source': 'profile.experience_years' if contradicted else '',
                     'evidence_quote': str(total_years) if contradicted else '',
                     'explanation': ('Declared total tenure is an upper bound below this scoped minimum.' if contradicted else
                                     'Total tenure cannot establish years in this specific responsibility; verify dated scope evidence.')})
    return rows


def _formal_requirement_satisfied(requirement, profile):
    kind = requirement.get('kind')
    if kind == 'education':
        return education_satisfied(requirement, profile)
    if kind == 'enrolment':
        entries = [*(profile.get('education') or []),
                   *((profile.get('cv_career_facts') or {}).get('education') or [])]
        return any((row.get('current') is True or str(row.get('status', '')).casefold().replace('-', '_') in
                    {'in_progress', 'enrolled', 'ongoing'}) and row.get('current') is not False
                   for row in entries)
    if kind == 'certification':
        value = str(requirement.get('value') or '')
        generic = value.casefold() in {'certificate', 'certification', 'zertifikat', 'zertifizierung', 'zertifiziert'}
        return any((_pattern(credential).search(requirement['evidence']) if generic else _pattern(value).search(credential))
                   and not re.search(r'\b(?:expired|revoked|abgelaufen|not held|nicht vorhanden)\b', credential, re.I)
                   for credential in profile.get('certifications') or [])
    return False


def required_application_artifact(text, *, mandatory=False):
    """Recognize requested application materials, preserving local waiver scope."""
    hit = re.search(r'\b(?:portfolio(?!\s+(?:management|analysis|risk|strategy|software|website)\b)|work samples?|Arbeitsproben?)\b', text, re.I)
    if not hit or requirement_polarity(text, hit.start(), hit.end()) != 'required':
        return False
    if re.search(r'\b(?:optional|if available|may bring|kann|freiwillig)\b', text, re.I):
        return False
    return mandatory or bool(re.search(
        r'\b(?:required|mandatory|must|submit|provide|attach|bring|einreichen|vorlegen|mitbringen|erforderlich)\b', text, re.I))


def _applicability_context(job, profile, enriched):
    """Explain unverified scope and mandatory logistics without inventing facts.

    Generic service skills become substantive only when service is the main
    work. Adjacent support work is a possible lead, never deployment expertise.
    """
    clauses = list(scoped_sentences(structured_text(job.get('description'))))
    raw = job.get('raw_metadata') or {}
    for prefix, values in [('structured', raw), ('detail', raw.get('detail_requirements') or {})]:
        for field in ('responsibilities', 'duties', 'qualifications', 'educationRequirements',
                      'experienceRequirements'):
            for source, text in structured_requirement_texts({field: values.get(field)}, prefix):
                clauses.extend(scoped_sentences(text, initial_scope=structured_source_scope(source)))
    duties = [text for text, scope in clauses if scope == 'duties']
    duties = list(dict.fromkeys([*[text for text in duties if not other_actor(text)],
                                *[row['job_quote'] for row in task_units(job)]]))
    main = ' '.join(duties)
    skills = set(normalize_skills(profile.get('skills') or []))
    mentions = {row['name'] for row in enriched['skills'] if row['kind'] == 'mentioned'}
    capabilities = skills | {skill for skill, weight, _ in _profile_content_skills(profile_content_sources(profile)) if weight == 1}
    uncovered = []
    duty_predicates = [part for text in duties for part in re.split(
        r'\b(?:and|und|also)\s+(?=(?:design|engineer|develop|diagnose|dispense|prescribe|commission|calculate)\b)', text, flags=re.I)]
    for text in duty_predicates:
        specialist = (re.search(r'\b(?:diagnose|dispense|prescribe|commission|calculate)\b', text, re.I) or
                      (re.search(r'\b(?:design|engineer|develop)\b', text, re.I) and
                       (DOMAIN.search(text) or re.search(r'\brelay|\bfault currents|\bsubstation|\bprotection|\barchitectur', text, re.I))))
        specific = set(extract_profile_skills(text)) - _GENERAL_SKILLS - {'Figma'}
        if specialist and not specific.intersection(capabilities):
            uncovered.append(text)
    service = bool(re.search(r'operational support|provide (?:\w+\s+){0,2}customer (?:service|support)|'
                             r'handle (?:\w+\s+){0,2}customer requests|deliver onboarding', duties[0] if duties else '', re.I))
    direct = service and not uncovered and {'Customer service', 'Onboarding'} <= mentions & skills
    adjacent = (not direct and 'Customer service' in skills and
                bool(re.search(r'assist customers|client communication|customer communication', main, re.I)))
    observations, unknowns = [], []
    academic_bridge = False
    eligibility_conditions = []
    application_review = False
    location_review = False
    gates = {'location': {'status': 'pass', 'reason': 'No mandatory workplace condition was stated.'},
             'eligibility': {'status': 'pass', 'reason': 'No mandatory formal or legal prerequisite was stated.'}}
    preferences = profile.get('application_preferences') or (profile.get('application_details') or {}).get('preferences') or {}
    work_style = job.get('work_arrangement') or ('remote' if job.get('is_remote') else enriched.get('work_style'))
    if work_style in {'onsite', 'hybrid'}:
        refused = preferences.get('willing_to_work_onsite') is False
        gates['location'] = {'status': 'fail' if refused else 'unknown',
            'reason': ('Saved preferences explicitly decline required on-site attendance.' if refused else
                       'Verify attendance, commuting or relocation for the stated ' + work_style + ' workplace: ' +
                       str(job.get('location_text') or 'location not specified') + '.')}
    elif work_style == 'remote' and re.search(r'\b(?:must reside|must live|payroll|resident|residency)\b', ' '.join(text for text, _ in clauses), re.I):
        gates['location'] = {'status': 'unknown', 'reason': 'Remote employment has a residence or payroll restriction; verify the required location independently of work authorization.'}
    for text, scope in clauses:
        mandatory = scope == 'requirements' or bool(re.search(r'\bmandatory\b|\brequired\b|\bmust\b|\berforderlich\b|\bzwingend\b', text, re.I))
        if scope in {'benefits', 'optional'}:
            continue
        if required_application_artifact(text, mandatory=mandatory):
            reason = 'Prepare and verify the requested work samples or portfolio: ' + text
            unknowns.append(reason)
            eligibility_conditions.append({'kind': 'application_artifact', 'status': 'unknown',
                                           'reason': reason, 'job_quote': text})
            application_review = True
        residence = re.search(r'\b(?:must reside|must live|residency is required|required residence|Wohnsitz erforderlich|Wohnsitz in)\b', text, re.I)
        if residence and requirement_polarity(text, residence.start(), residence.end()) == 'required' and (mandatory or re.search(r'\bmust\b', residence.group(), re.I)):
            location_review = True
            gates['location'] = {'status': 'fail' if gates['location']['status'] == 'fail' else 'unknown',
                'reason': (gates['location']['reason'] + ' ' if gates['location']['status'] == 'fail' else '') +
                          'Verify the required residence or payroll jurisdiction separately from work authorization: ' + text}
            unknowns.append(gates['location']['reason'])
        if OPTIONAL.search(text) and not re.search(r'\bmandatory\b|\bmust\b', text, re.I):
            continue
        conditions = _eligibility_conditions(text, preferences, profile.get('certifications') or [], mandatory)
        license_required = any(row['kind'] == 'license' for row in conditions)
        authorization = any(row['kind'] == 'authorization' for row in conditions)
        scoped_tenure = mandatory and re.search(r'leadership|leading|managing managers|department heads|budget|commercial|clinical|paid|professional|engineering|research|führung|führungsverantwortung|berufserfahrung', text, re.I)
        academic = mandatory and re.search(r'academic (?:project )?exposure|university project', text, re.I)
        core_unknown = (scope == 'duties' and not direct and not adjacent and not OPTIONAL.search(text) and
                        bool(re.search(r'\b(?:own|manage|approve|design|build|deliver|maintain|respond|coordinate|assist|dispense|verify)\b', text, re.I)) and
                        not any(row['kind'] == 'mentioned' for row in enriched['skills']))
        if scoped_tenure:
            rows = _scoped_experience_rows(text, profile.get('experience_years'))
            observations.extend(rows)
            unknowns.extend('Verify scoped tenure: ' + row['requirement'] + ' — ' + row['explanation'] for row in rows)
            eligibility_conditions.extend({'kind': 'experience', 'status': 'fail' if row['status'] == 'missing' else 'unknown',
                'reason': ('Declared total tenure is below the required scoped minimum: ' if row['status'] == 'missing' else
                           'Required responsibility-specific tenure is unverified: ') + row['requirement']} for row in rows)
        if academic:
            for skill in sorted(skills):
                if not _pattern(skill).search(text):
                    continue
                context = _skill_context(profile, skill)
                supported = context['context'] in {'academic', 'research'}
                observations.append({'kind': 'academic', 'requirement': text, 'status': 'supported' if supported else 'unknown',
                    'job_source': 'description', 'job_quote': text, 'evidence_source': context['source'],
                    'evidence_quote': context['quote'], 'evidence_context': context['context'],
                    'explanation': ('Saved academic/research scope supports this prerequisite.' if supported else
                                    'The tool is declared, but its saved source does not verify academic-project scope. Commercial production experience is not substituted for the academic prerequisite.')})
                academic_bridge = True
                if not supported:
                    unknowns.append('Verify academic project scope for ' + skill + ' at ' + context['source'])
        if core_unknown:
            for part in re.split(r',|\b(?:and|und)\b', text):
                requirement = re.sub(r'^(?:core work|your role|your responsibilities|your tasks):\s*', '', part.strip(), flags=re.I)
                if not re.search(r'\b(?:own|manage|approve|design|build|deliver|maintain|respond|coordinate|assist|dispense|verify)\b', requirement, re.I):
                    continue
                observations.append({'kind': 'responsibility', 'requirement': requirement, 'status': 'unknown',
                    'job_source': 'description', 'job_quote': part.strip(), 'evidence_source': '', 'evidence_quote': '',
                    'explanation': 'Verify evidence for this responsibility and decision authority; title or generic support tools do not establish it.'})
                unknowns.append('Verify responsibility scope: ' + requirement)
        if any(row['status'] != 'pass' for row in conditions):
            observation = {'requirement': text, 'status': 'unknown', 'job_source': 'description',
                           'explanation': 'Verify the exact qualification and scope against saved evidence; a skill or desired title alone is insufficient.'}
            observations.append(observation)
            unknowns.append('Verify ' + ('mandatory credential: ' if license_required else
                                       'jurisdiction-specific work authorization: ' if authorization else
                                       'scope-specific evidence: ') + text)
        eligibility_conditions.extend(conditions)
    for text in duties:
        if re.search(r'\bbudget', text, re.I) and not any(text in row for row in unknowns):
            unknowns.append('Verify budget responsibility and decision authority: ' + text)
    if adjacent:
        unknowns.append('Verify product-specific implementation knowledge; adjacent service work does not establish deployment or architecture ownership.')
    unknowns.extend('Verify uncovered specialist duties: ' + text for text in uncovered)
    for requirement in enriched.get('requirements', []):
        kind = requirement.get('kind')
        if kind not in {'education', 'certification', 'enrolment'}:
            continue
        satisfied = _formal_requirement_satisfied(requirement, profile)
        label = {'education': 'degree', 'certification': 'certificate', 'enrolment': 'current student enrolment'}[kind]
        condition = {'kind': kind, 'status': 'pass' if satisfied else 'unknown',
                     'reason': ('Saved ' + label + ' evidence supports: ' if satisfied else
                                'Required ' + label + ' is unverified: ') + requirement['evidence']}
        condition.update(qualification=dict(requirement), job_source=requirement['source'],
                         job_quote=requirement['evidence'],
                         unknown_messages=['Verify requirement: ' + requirement['evidence']])
        eligibility_conditions.append(condition)
        observations.append({'kind': kind, 'status': 'supported' if satisfied else 'unknown',
                             'requirement': requirement['evidence'],
                             'job_source': requirement['source'], 'job_quote': requirement['evidence'],
                             'explanation': condition['reason']})
    if eligibility_conditions:
        status = 'fail' if any(row['status'] == 'fail' for row in eligibility_conditions) else 'unknown' if any(row['status'] == 'unknown' for row in eligibility_conditions) else 'pass'
        gates['eligibility'] = {'status': status, 'reason': ' '.join(dict.fromkeys(row['reason'] for row in eligibility_conditions if row['status'] == status))[:500]}
    blocking = any(row['status'] != 'pass' for row in eligibility_conditions) or application_review or location_review
    substantive = bool(mentions or duties)
    return {'score_floor': 85 if direct else 40 if adjacent or academic_bridge else 0,
            'transferable': adjacent, 'direct_service': direct, 'substantive': substantive,
            'observations': observations, 'unknowns': unknowns, 'gates': gates, 'blocking': blocking,
            'uncovered_specialist': bool(uncovered),
            'prerequisite_conditions': eligibility_conditions + ([{'kind': 'residence', 'gate': 'location',
                **gates['location']}] if location_review else [])}


def match_job(job: dict, profile: dict, enrichment: dict | None = None, *, semantic: dict | None = None) -> dict:
    enriched = enrichment or enrich_job(job)
    profile_skills = normalize_skills(profile.get('skills', []))
    narrative = _profile_content_skills(profile_content_sources(profile))
    job_skills = [row['name'] for row in enriched['skills']]
    # Explicit user skills outside the small vocabulary still get literal matching.
    title = plain_text(job.get('title'))
    raw = job.get('raw_metadata') or {}
    custom_texts = [('title', title), ('description', structured_text(job.get('description')))]
    for prefix, data in [('structured',raw), ('detail',raw.get('detail_requirements') or {})]:
        for key in ('skills','qualifications'):
            if data.get(key):
                custom_texts.extend(structured_requirement_texts({key: data[key]}, prefix))
    custom_evidence = []
    for skill in profile_skills:
        if skill not in SKILLS:
            best = None
            best_rank = -1
            for source, evidence_text in custom_texts:
                for found in _pattern(skill).finditer(evidence_text):
                    kind = _mention_kind(evidence_text, found, source)
                    rank = {'incidental': 0, 'title': 1, 'optional': 2, 'mentioned': 3}[kind]
                    if rank > best_rank:
                        best = {**_evidence(skill, evidence_text, found, source), 'name': skill, 'kind': kind}
                        best_rank = rank
                    if best_rank == 3:
                        break
                if best_rank == 3:
                    break
            if best:
                job_skills.append(skill)
                custom_evidence.append(best)
    matched = [name for name in profile_skills if name in job_skills]
    missing = [name for name in job_skills if name not in profile_skills]
    roles = [role for role in profile.get('desired_roles', []) if _role_match(role, title)]
    secondary_roles = [role for role in profile.get('secondary_roles', []) if _role_match(role, title)]
    conflicts, unknowns, reasons = [], [], []
    for key, field, label in [('work_styles', 'work_style', 'Work style'), ('employment_types', 'employment_types', 'Employment type'), ('seniority_levels', 'seniority_levels', 'Experience level')]:
        preferences = profile.get(key) or []
        value = enriched.get(field)
        if isinstance(value, dict):
            value = value.get('value')
        if preferences and not value:
            unknowns.append(label + ' not stated')
        elif preferences and (not set(value).intersection(preferences) if isinstance(value,list) else value not in preferences):
            conflicts.append(label + ': ' + str(value).replace('_', ' '))
    experience = enriched.get('experience_years')
    experience_adjustment = 0
    profile_experience = profile.get('experience_years')
    if experience and profile_experience is None:
        unknowns.append('Profile experience not provided')
    elif experience and experience['value'] > profile_experience:
        conflicts.append(f"Requires at least {experience['value']:g} years; profile has {profile_experience:g}")
        experience_adjustment = -10
    elif experience:
        reasons.append(f"Total declared experience reaches the numeric minimum ({profile_experience:g} years vs {experience['value']:g}); verify the required work scope")
        experience_adjustment = 5
    language_levels = {name.casefold(): str(level).upper() for name, level in (profile.get('language_levels') or {}).items()}
    known_languages = {x.casefold() for x in profile.get('languages', [])} | set(language_levels)
    if enriched['languages'] and not known_languages:
        unknowns.append('Profile languages not provided')
    language_groups = {}
    language_adjustment = 0
    for index, language in enumerate(enriched['languages']):
        language_groups.setdefault(language.get('alternative_group') or f'mandatory:{index}', []).append(language)
    for group in language_groups.values():
        outcomes = []
        for language in group:
            name = language['value']
            level = language.get('level')
            inferred_level = bool(language.get('level_inferred'))
            level_label = _language_level_label(language)
            candidate_level = language_levels.get(name.casefold())
            level_range = language.get('level_range') or []
            if known_languages and name.casefold() not in known_languages:
                outcomes.append(('unknown', name + ' required; profile language evidence not provided', inferred_level))
            elif level and candidate_level in CEFR_RANK and CEFR_RANK[candidate_level] < CEFR_RANK[level]:
                # Qualitative fluency ranges are estimates, not employer-stated
                # CEFR minima. Preserve the gap as review rather than a denial.
                business_review = inferred_level
                outcomes.append(('unknown' if business_review else 'conflict',
                    f'{level_label} required; profile has {candidate_level}' +
                    ('; verify the employer’s required proficiency' if business_review else ''), inferred_level))
            elif level and not candidate_level:
                outcomes.append(('unknown', f'{level_label} required; profile level not provided', inferred_level))
            elif not level and name.casefold() in known_languages:
                if language.get('kind') == 'language_requirement':
                    outcomes.append(('met', f'{name} listed in profile', inferred_level))
                else:
                    outcomes.append(('unknown', f'{name} fluency requested; exact level not stated', inferred_level))
            elif not known_languages:
                outcomes.append(('unknown', 'Profile languages not provided', inferred_level))
            else:
                outcomes.append(('met', level_label if level else f'{name} language', inferred_level))
        if len(group) > 1:
            # A satisfied OR option resolves the whole clause. An unverified option
            # prevents hard rejection, while independent mandatory rows still apply.
            met = next((message for status, message, _ in outcomes if status == 'met'), None)
            if met:
                reasons.append('Language alternative met: ' + met)
                language_adjustment += 5
            elif any(status == 'unknown' for status, _, _ in outcomes):
                unknowns.append('Verify language alternative: ' + ' or '.join(message for _, message, _ in outcomes))
            else:
                conflicts.append('Language alternatives unmet: ' + ' or '.join(message for _, message, _ in outcomes))
                language_adjustment -= 6 if all(inferred for _, _, inferred in outcomes) else 10
        else:
            status, message, inferred_level = outcomes[0]
            if status == 'conflict':
                conflicts.append(message)
                language_adjustment -= 6 if inferred_level else 10
            elif status == 'unknown' and message not in unknowns:
                unknowns.append(message)
            elif status == 'met':
                reasons.append('Language requirement met: ' + message)
                language_adjustment += 5
    requirement_gaps = []
    gap_penalties = {}
    verified_requirements = []
    evidence_by_skill = {normalize_skills([name])[0].casefold(): row
                         for name, row in (profile.get('skill_evidence') or {}).items()}
    profile_skill_names = {skill.casefold() for skill in profile_skills}
    for requirement in enriched.get('deferred_requirements', []):
        # Preserve the unresolved condition without making it a hard gap or
        # affecting scores. Only explicitly named profile evidence can clear it.
        members = normalize_skills(re.split(r',|\b(?:and|und|or|oder)\b', requirement['value'], flags=re.I))
        satisfied = any(member.casefold() in profile_skill_names or
                        str(evidence_by_skill.get(member.casefold(), {}).get('context', '')).casefold() == 'professional'
                        for member in members)
        if not satisfied:
            unknown = 'Verify scripting alternative: ' + requirement['evidence']
            if unknown not in unknowns:
                unknowns.append(unknown)
    for requirement in enriched.get('requirements', []):
        value = str(requirement['value'])
        kind = requirement.get('kind', 'qualification')
        evidence = evidence_by_skill.get(value.casefold())
        satisfied = False
        if kind in {'education', 'certification', 'enrolment'}:
            satisfied = _formal_requirement_satisfied(requirement, profile)
        elif kind not in {'enrolment', 'professional_experience'}:
            satisfied = value.casefold() in profile_skill_names or bool(
                evidence and str(evidence.get('context', '')).casefold() == 'professional')
        elif kind == 'professional_experience':
            satisfied = bool(evidence and str(evidence.get('context', '')).casefold() == 'professional')
        if satisfied:
            verified_requirements.append(requirement)
            if kind == 'education':
                reasons.append('Education evidence meets stated degree requirement: ' + requirement['evidence'])
            elif kind == 'certification':
                reasons.append('Certification evidence: ' + requirement['evidence'])
            continue
        prefix = ('Verify current student enrolment: ' if kind == 'enrolment' else
                  'Verify specialist role focus: ' if kind == 'title_domain' else 'Verify requirement: ')
        gap = prefix + requirement['evidence']
        if gap not in requirement_gaps:
            requirement_gaps.append(gap)
            unknowns.append(gap)
        penalty = {'domain': 25, 'education': 20, 'certification': 15,
                   'enrolment': 30, 'professional_experience': 20, 'skill': 15, 'title_domain': 25}.get(kind, 15)
        if kind not in {'education', 'certification', 'enrolment'}:
            gap_penalties[gap] = max(gap_penalties.get(gap, 0), penalty)
    if experience and profile.get('experience_years') is None:
        gap = f"Verify minimum relevant experience: {experience['value']:g} years; CV exact months not established"
        requirement_gaps.append(gap)
        unknowns.append(gap)
        if not any(row['evidence'] == experience['evidence'] and row.get('kind') in {'domain', 'professional_experience'}
                   for row in enriched.get('requirements', [])):
            gap_penalties[gap] = 15
    if (roles or secondary_roles or matched or narrative) and profile.get('experience_years') is None and (enriched.get('seniority') or {}).get('value') in {'senior', 'lead'}:
        gap = 'Verify senior-level professional tenure: exact relevant CV months not established'
        requirement_gaps.append(gap)
        unknowns.append(gap)
        gap_penalties[gap] = 15
    for term in profile.get('excluded_terms', []):
        if _pattern(term).search(plain_text(job.get('title'))):
            conflicts.append('Excluded title term: ' + term)
    if enriched['quality'] == 'limited':
        unknowns.append('Full job description needs checking')
    if profile_skills and not job_skills:
        unknowns.append('Skills not stated or not recognized')
    has_role_preferences = bool(profile.get('desired_roles') or profile.get('secondary_roles'))
    score = 55 if roles else 35 if secondary_roles else 0
    academic_matches = []
    substantive_matches = []
    incidental_matches = []
    optional_matches = []
    title_matches = []
    job_evidence = {row['name']: row for row in enriched['skills']}
    job_evidence.update({row['name']: row for row in custom_evidence})
    weighted_matches = 0.0
    for skill in matched:
        entry = evidence_by_skill.get(skill.casefold()) or {}
        context = str(entry.get('context', '')).casefold()
        kind = job_evidence.get(skill, {}).get('kind', 'mentioned')
        if context in {'academic', 'research'}:
            academic_matches.append((skill, context))
        if kind == 'incidental':
            incidental_matches.append(skill)
        elif kind == 'title':
            title_matches.append(skill)
            if not roles and not secondary_roles:
                weighted_matches += .4 * (.5 if context in {'academic', 'research'} else 1)
        elif kind == 'optional':
            optional_matches.append(skill)
            weighted_matches += .25 * (.5 if context in {'academic', 'research'} else 1)
        else:
            substantive_matches.append(skill)
            weighted_matches += .5 if context in {'academic', 'research'} else 1
    if matched:
        # Job evidence determines coverage; a broad CV is not penalized for extra skills.
        coverage = weighted_matches / max(1, len(set(job_skills)))
        score += (45 if roles else 20 if secondary_roles else 25 if has_role_preferences else 100) * coverage
        if substantive_matches:
            reasons.append('Skills mentioned: ' + ', '.join(substantive_matches))
        if optional_matches:
            reasons.append('Optional skills mentioned: ' + ', '.join(optional_matches))
        if incidental_matches:
            reasons.append('Incidental mentions: ' + ', '.join(incidental_matches))
        if title_matches:
            reasons.append('Title mentions: ' + ', '.join(title_matches))
        reasons.extend(f'{skill} evidence is {context} or project based' for skill, context in academic_matches)
    if roles:
        reasons.extend('Role matches ' + role for role in roles)
    elif secondary_roles:
        reasons.extend('Secondary role matches ' + role for role in secondary_roles)
    content = _content_overlap(job_evidence, profile_skills, evidence_by_skill, narrative)
    if content['score']:
        score = max(score, content['score'])
        reasons.append('Job content overlaps profile capabilities: ' + ', '.join(
            row['skill'] for row in content['matches'] if row['kind'] == 'mentioned'))
        for row in content['matches']:
            if row['profile_source'] != 'skills':
                source = ('work history' if row['profile_source'].startswith('experience.') else
                          'career evidence' if row['profile_source'] == 'career_evidence' else 'summary')
                reasons.append(f"{row['skill']} found in profile {source}; qualification evidence still needs checking")
    applicability = _applicability_context(job, profile, {**enriched, 'skills': list(job_evidence.values())})
    task_alignment = align_tasks(job, profile_content_sources(profile), source_contexts={
        f'skill_evidence.{name}.note': row.get('context', 'unspecified')
        for name, row in (profile.get('skill_evidence') or {}).items() if isinstance(row, dict)})
    academic_policy = []
    for sentence, _ in scoped_sentences(structured_text(job.get('description'))):
        if is_source_instruction(sentence):
            continue
        hit = re.search(r'\b(?:academic|university|student|coursework|Studienprojekte|Studierende)\b.{0,65}'
                        r'\b(?:accepted|welcome|count|sufficient|zählen|reichen|ausreichend|willkommen|anerkannt)\b',
                        sentence, re.I)
        if hit:
            negative_policy = re.search(
                r'\b(?:not|never)\s+(?!(?:only|just)\b)[^.;!?]{0,100}?\b(?:accepted|welcome|count|sufficient)\b|'
                r'\bnicht\s+(?!nur\b)[^.;!?]{0,100}?\b(?:anerkannt|ausreichend|willkommen)\b|'
                r'\b(?:zählen|reichen)\s+nicht\b', sentence, re.I)
            academic_policy.append(not bool(negative_policy))
    academic_admissible = (False not in academic_policy and
                           ((enriched.get('seniority') or {}).get('value') in {'junior', 'student'} or
                            True in academic_policy))
    grounded_tasks = (len(task_alignment['requirements']) >= 2 and
                      (task_alignment['score'] or 0) >= 65 and
                      any(row['status'] == 'supported' for row in task_alignment['requirements']) and
                      (any(row['status'] == 'supported' and row['evidence_context'] != 'academic'
                           for row in task_alignment['requirements']) or
                       (academic_admissible and (enriched.get('seniority') or {}).get('value') not in {'senior', 'lead'} and
                        not any(row.get('kind') == 'professional_experience' for row in enriched.get('requirements', [])))))
    partial_tasks = any(row['status'] in {'supported', 'transferable'} for row in task_alignment['requirements'])
    if not grounded_tasks and task_alignment['score'] and partial_tasks:
        # Proportional partial evidence cannot recommend a role on its own.
        score = max(score, min(59, task_alignment['score']))
        reasons.append('Partial task evidence overlaps documented work; verify the remaining duties and qualifications.')
    if grounded_tasks:
        score = max(score, min(85, task_alignment['score']))
    for row in task_alignment['requirements']:
        if row['status'] in {'supported', 'transferable'}:
            label = ('Transferable task evidence (complete scope unverified): ' if row['status'] == 'transferable' else
                     'Supported academic/project task evidence: ' if row['evidence_context'] == 'academic' else
                     'Supported task evidence: ')
            reasons.append(label + row['job_quote'] + ' Source: ' + row['evidence_source'] + ' — ' + row['evidence_quote'])
    score = max(score, applicability['score_floor'])
    if applicability['direct_service']:
        reasons.append('Service and onboarding capabilities overlap the core support duties; specialist ownership is not implied.')
    if applicability['transferable']:
        reasons.append('Transferable service experience overlaps client communication and implementation support; product-specific scope needs verification.')
    unknowns.extend(applicability['unknowns'])
    for row in applicability['observations']:
        if row.get('kind') == 'experience' and row['status'] == 'missing':
            conflicts.append('Declared total tenure is below the scoped minimum: ' + row['requirement'])
    for gate in applicability['gates'].values():
        if gate['status'] == 'fail':
            conflicts.append(gate['reason'])
    content_skills = [row['skill'] for row in content['matches']]
    matched = list(dict.fromkeys([*[skill for skill in matched if skill not in incidental_matches], *content_skills]))
    missing = [skill for skill in missing if skill not in content_skills]
    lexical_score = score
    semantic_boosted = False
    if semantic and semantic.get('score', 0) > 0:
        semantic_score = max(0, min(59, semantic['score']))
        if semantic_score > score:
            score = semantic_score
            semantic_boosted = True
        reasons.append('Local semantic overlap: ' + semantic.get('job_evidence', '')[:300])
    # A title-only listing cannot imply a strong fit, even with matching skills or languages.
    score_limit = 100
    if applicability['uncovered_specialist']:
        score_limit = 29
    if any(row.get('kind') == 'experience' for row in applicability['observations']):
        score_limit = min(score_limit, 29 if any(row.get('kind') == 'experience' and row['status'] == 'missing'
                                              for row in applicability['observations']) else 59)
    if not applicability['substantive']:
        score_limit = 29 if re.search(r'only the title|contact recruiting for', job.get('description') or '', re.I) else 59
        unknowns.append('Substantive responsibilities and qualification requirements are unavailable; a desired title is not task evidence.')
    if enriched['quality'] == 'limited' and not applicability['direct_service'] and not grounded_tasks:
        score_limit = min(score_limit, 60)
    if roles and len(substantive_matches) < 2:
        score_limit = min(score_limit, 85)
    if (roles and substantive_matches and set(substantive_matches) <= {'Customer service', 'Onboarding'}
            and not _role_match('Customer service', title)
            and not verified_requirements and content['score'] < 65):
        score_limit = min(score_limit, 64)
    if secondary_roles and not roles and content['score'] < 65:
        score_limit = min(score_limit, 59)
    fully_remote = (job.get('work_arrangement') in {None, 'remote'} and
                    (job.get('is_remote') is True or job.get('work_arrangement') == 'remote'))
    vote_results = [] if fully_remote else job_city_vote_results(job)
    scoring_vote_results = [row for row in vote_results if row['postal_ballots_included']]
    defaults = profile.get('matching_defaults') or {}
    try:
        location_weight = max(0, min(20, int(defaults.get('low_afd_vote_share_weight', 0))))
    except (TypeError, ValueError):
        location_weight = 0
    average_vote_share = (round(sum(row['vote_share_percent'] for row in scoring_vote_results) /
                                len(scoring_vote_results), 2) if scoring_vote_results else None)
    location_adjustment = (max(-location_weight, min(location_weight,
                           round((25 - average_vote_share) / 25 * location_weight)))
                           if average_vote_share is not None and location_weight else 0)
    def adjusted_score(value, *, include_language=True):
        value = min(100, round(value))
        if gap_penalties:
            # Scope gaps limit demonstrated role evidence; formal prerequisites
            # are separate gates, not evidence that documented duties disappeared.
            value = max(0, min(value, 60) - min(40, sum(gap_penalties.values())))
        value += max(-10, min(5, experience_adjustment))
        if include_language:
            value += max(-20, min(10, language_adjustment))
        value = max(0, min(score_limit, value))
        return max(0, min(score_limit, value + location_adjustment))

    # Desired titles, language and location preferences do not demonstrate duties.
    task_evidence_score = max(content['score'], applicability['score_floor'],
                              (task_alignment['score'] or 0) if partial_tasks else 0)
    task_fit = max(0, min(29 if applicability['uncovered_specialist'] else 100,
                         round(task_evidence_score))) if applicability['substantive'] else 0
    qualification_fit = min(85, task_fit) if applicability['substantive'] else 0
    score = adjusted_score(score)
    if semantic_boosted:
        baseline = adjusted_score(lexical_score)
        # Language, tenure and location bonuses cannot turn semantic similarity
        # into a recommendation. Independently qualifying rule matches survive.
        score = min(score, max(59, baseline))
        if score > baseline:
            unknowns.append('Related work found by local semantic similarity; verify transferable experience')
    if location_adjustment:
        direction = 'increased' if location_adjustment > 0 else 'reduced'
        cities = ', '.join(row['municipality'] for row in scoring_vote_results)
        reasons.append(
            f"Location preference {direction} this score by {abs(location_adjustment)} points using "
            f"the 2025 federal-election AfD second-vote results for {cities}."
        )
    if not enriched['languages']:
        for text, scope in scoped_sentences(structured_text(job.get('description'))):
            if scope in {'optional', 'benefits'} or OPTIONAL.search(text):
                continue
            if not re.search(r'meetings?|minutes|communication|working language|Kundensitzungen|Protokolle|Besprechungen|Kommunikation', text, re.I):
                continue
            names = [name for name, pattern in LANGUAGE_PATTERNS.items() if pattern.search(text)]
            if names:
                unknowns.append('Verify working-language proficiency for ' + ', '.join(names) + '; no explicit CEFR minimum was stated.')
    language_names = [row['value'].casefold() for row in enriched['languages']]
    language_notes = [message for message in unknowns if 'language' in message.casefold() or
                      any(name in message.casefold() for name in language_names)]
    language_conflicts = [message for message in conflicts if 'language' in message.casefold() or
                          any(name in message.casefold() for name in language_names)]
    language_gate = {'status': 'fail' if language_conflicts else 'unknown' if language_notes else 'pass',
                     'reason': '; '.join(language_conflicts or language_notes)[:500] or
                               ('Saved language evidence satisfies the extracted requirement.' if enriched['languages'] else 'No mandatory language requirement was stated.')}
    language_review = language_gate['status'] == 'unknown'
    fit_tier = ('unlikely' if conflicts or score < 30 or applicability['blocking'] else
                'recommended' if score >= 65 and task_fit >= 65 and not language_review and (enriched['quality'] != 'limited' or applicability['direct_service'] or grounded_tasks) and not requirement_gaps else
                'possible')
    return {'score': score, 'eligible': not conflicts, 'uncertain': bool(unknowns),
            'mandatory_review': applicability['blocking'],
            'language_review': language_review,
            'qualification_fit': qualification_fit,
            'task_fit': task_fit,
            'task_alignment': task_alignment,
            'gates': {**applicability['gates'], 'language': language_gate},
            'requirement_observations': applicability['observations'],
            'prerequisite_conditions': applicability['prerequisite_conditions'],
            'profile_skill_context': {skill: _skill_context(profile, skill) for skill in matched},
            'fit_tier': fit_tier, 'requirement_gaps': requirement_gaps,
            'matched_skills': matched, 'missing_skills': missing, 'reasons': reasons,
            'custom_skill_evidence': custom_evidence, 'content_match': content,
            'semantic_match': semantic if semantic and semantic.get('score', 0) > 0 else None,
            'local_context': {'city_election_results': vote_results,
                              'average_afd_vote_share_percent': average_vote_share,
                              'score_adjustment': location_adjustment,
                              'weight': location_weight},
            'conflicts': conflicts, 'unknowns': unknowns,
            'label': 'Preference conflict' if conflicts else 'Strong overlap' if score >= 65 else 'Some overlap' if score >= 30 else 'Little evidence of a match'}


def _identity_title(title: str) -> str:
    gender = r'\((?:[mwdfh]\s*[/|&]\s*){1,3}[mwdfh]\)|\((?:all genders|gn|divers)\)'
    return ' '.join(re.sub(gender, '', title, flags=re.I).casefold().split())


def _identity_url(url: str) -> str:
    value = urlsplit(url)
    return urlunsplit((value.scheme.lower(), value.netloc.lower(), value.path.rstrip('/'), value.query, value.fragment))


def detail_updates(job: dict, body: bytes | str, page_url: str) -> dict:
    """Hydrate only an unambiguous JSON-LD posting matching both URL and title."""
    doc = Document()
    doc.feed(body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body)
    candidates = []

    def visit(value):
        if isinstance(value, list):
            for row in value: visit(row)
        elif isinstance(value, dict):
            types = value.get('@type')
            if types == 'JobPosting' or isinstance(types, list) and 'JobPosting' in types:
                candidate_url = value.get('url') or page_url
                if not isinstance(candidate_url, str):
                    return
                if (_identity_url(urljoin(page_url, candidate_url)) in {_identity_url(job['url']), _identity_url(page_url)} and
                    _identity_title(plain_text(value.get('title'))) == _identity_title(job['title'])):
                    candidates.append(value)
            for row in value.values():
                if isinstance(row, (dict, list)): visit(row)
    for script_type, source in doc.scripts:
        if 'ld+json' not in script_type:
            continue
        try:
            visit(json.loads(source))
        except (ValueError, TypeError, RecursionError):
            continue
    if not candidates:
        return _html_detail_updates(job, body, page_url)
    if len(candidates) != 1:
        return {}
    posting = candidates[0]
    description = structured_text(posting.get('description'))
    verified = (job.get('raw_metadata') or {}).get('description_method') in {'verified_detail_jsonld','verified_detail_html'}
    if not description or (not verified and len(description) <= len(plain_text(job.get('description')))):
        return {}
    raw = dict(job.get('raw_metadata') or {})
    raw['detail_requirements'] = {key: posting[key] for key in ('skills', 'qualifications', 'experienceRequirements', 'educationRequirements') if key in posting}
    if 'detail_listing_description' not in raw:
        raw['detail_listing_description'] = job.get('description')
    raw['description_evidence_url'] = page_url
    raw['description_method'] = 'verified_detail_jsonld'
    return {'description': description, 'raw_metadata': raw}


def _html_detail_updates(job: dict, body: bytes | str, page_url: str) -> dict:
    """Fallback to a single matching job heading in main/article, with role sections.

    Shared overview/fragment URLs, unrelated roles and site navigation cannot supply
    a description. Explicit 'other jobs' sections terminate the role text.
    """
    from hiring_scraper.html_jobs import _TreeParser, _Element
    parsed_url = urlsplit(page_url)
    if parsed_url.fragment or urlsplit(job['url']).fragment:
        return {}
    if not re.search(r'/(?:jobs?|stellenangebote?|jobangebote?|vacanc(?:y|ies)|positions?)/[^/]+', parsed_url.path, re.I):
        return {}
    parser = _TreeParser()
    parser.feed(body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body)
    scopes = [node for node in parser.root.walk() if node.tag in {'main','article'} and not node.hidden]
    if len(scopes) > 20:
        return {}
    scopes.sort(key=lambda node: len(node.text()))
    for node in scopes:
        headings = [child for child in node.walk() if child.tag == 'h1' and not child.hidden]
        if len(headings) != 1 or _identity_title(headings[0].text()) != _identity_title(job['title']):
            continue
        def narrative(element):
            if element.hidden or element.tag in {'nav','footer','aside','form'}:
                return ''
            content = ' '.join(narrative(child) if isinstance(child, _Element) else child for child in element.children)
            return '\n' + content + '\n' if element.tag in {'p','div','li','section','h1','h2','h3','br'} else content
        text = structured_text(narrative(node))
        start = text.find(headings[0].text())
        if start < 0:
            continue
        text = text[start:]
        text = re.split(r'other jobs|related jobs|weitere stellen|nicht der richtige job|das ist nicht die passende stelle', text, maxsplit=1, flags=re.I)[0].strip()
        conventional_sections = re.search(r'your (?:responsibilities|profile|tasks)|responsibilities|qualifications|dein(?:e)? (?:profil|aufgaben)|ihr(?:e)? (?:profil|aufgaben)|anforderungen',text,re.I)
        paired_applicant_sections = (
            re.search(r'\bfolgende Aufgaben Dich begeistern\b', text, re.I) and
            re.search(r'\bfolgende Voraussetzungen mitbringst\b', text, re.I)
        )
        if len(text) < 350 or not (conventional_sections or paired_applicant_sections):
            continue
        verified = (job.get('raw_metadata') or {}).get('description_method') in {'verified_detail_jsonld','verified_detail_html'}
        if not verified and len(text) <= len(plain_text(job.get('description'))):
            continue
        raw = dict(job.get('raw_metadata') or {})
        # Any old detail-specific structured fields are invalid after a new HTML snapshot.
        raw['detail_requirements'] = {}
        if 'detail_listing_description' not in raw:
            raw['detail_listing_description'] = job.get('description')
        raw['description_evidence_url'] = page_url
        raw['description_method'] = 'verified_detail_html'
        return {'description':text[:15000], 'raw_metadata':raw}
    return {}
