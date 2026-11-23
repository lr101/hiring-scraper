"""Interpretable, domain-aware feature extraction and bounded soft penalties for v2.

All features lie in [0.0, 1.0]. Language and work-authorisation/visa clauses are
excluded from content features so ablating language/visa requirements cannot leak
into duty, requirement, skill, or domain scores. Every feature and penalty carries
source citations for the additive explanation ledger.
"""
from __future__ import annotations

from functools import lru_cache
from itertools import islice
import json
import math
import re
from typing import Any

from hiring_scraper.duty_evidence import analyze_duty_units, assess_duties
from hiring_scraper.matching import (
    EXPERIENCE_PATTERN,
    OPTIONAL,
    REQUIREMENT,
    ROLE_GROUPS,
    SKILLS,
    _experience_years,
    _is_experience_match,
    _pattern,
    _profile_skill_contexts,
    _skill_context,
    _scoped_experience_rows,
    extract_profile_skills,
    normalize_skills,
    plain_text,
    profile_content_sources,
)
from hiring_scraper.matching_v2.gates import (
    NON_REQUIREMENT_V2,
    is_language_or_visa_clause,
    job_requirement_texts,
    parse_job_requirement_texts,
    strip_language_or_visa_subclauses,
)
from hiring_scraper.matching_v2.weights import SOFT_PENALTIES
from hiring_scraper.requirements import (
    DEGREE,
    clause_key,
    education_evidence,
    is_source_instruction,
    qualification_rows,
    requirement_polarity,
    scoped_sentences,
    structured_source_scope,
    structured_text,
)
from hiring_scraper import task_alignment as _ta
from hiring_scraper.task_alignment import other_actor


# ---------------------------------------------------------------------------
# Expanded bilingual skill aliases & generic-skill set
# ---------------------------------------------------------------------------

EXTRA_SKILLS: dict[str, list[str]] = {
    'Jira': ['jira'],
    'Confluence': ['confluence'],
    'Notion': ['notion'],
    'Slack': ['slack'],
    'Miro': ['miro'],
    'Asana': ['asana'],
    'Monday.com': ['monday.com'],
    'HubSpot': ['hubspot'],
    'Zendesk': ['zendesk'],
    'Intercom': ['intercom'],
    'Workday': ['workday'],
    'Personio': ['personio'],
    'SuccessFactors': ['successfactors', 'sap successfactors'],
    'LMS': ['lms', 'learning management system', 'lernmanagementsystem', 'moodle', 'cornerstone', 'docebo'],
    'Articulate': ['articulate', 'storyline', 'rise 360', 'captivate', 'camtasia'],
    'Instructional design': ['instructional design', 'didaktik', 'didaktisch', 'lernkonzept', 'e-learning', 'elearning', 'blended learning'],
    'Agile': ['agile', 'agil', 'agile methods', 'agile methoden', 'sprint planning', 'backlog'],
    'Product strategy': ['product strategy', 'produktstrategie', 'product roadmap', 'produkt-roadmap', 'roadmap'],
    'OKR': ['okr', 'okrs', 'objectives and key results'],
    'A/B testing': ['a/b testing', 'ab testing', 'a/b-testing', 'experimentation'],
    'UX design': ['ux design', 'ui/ux', 'user experience', 'interaction design', 'usability testing'],
    'Design systems': ['design system', 'design systems'],
    'GraphQL': ['graphql'],
    'Kafka': ['kafka', 'apache kafka'],
    'Spark': ['apache spark', 'pyspark'],
    'Airflow': ['airflow', 'apache airflow'],
    'dbt': ['dbt'],
    'Snowflake': ['snowflake'],
    'BigQuery': ['bigquery'],
    'Pandas': ['pandas'],
    'NumPy': ['numpy'],
    'Scikit-learn': ['scikit-learn', 'sklearn'],
    'NLP': ['nlp', 'natural language processing', 'llm', 'llms', 'generative ai'],
    'Deep learning': ['deep learning'],
    'Statistics': ['statistics', 'statistik', 'statistical modeling', 'statistische modellierung'],
    'R': ['r programming', 'r studio', 'rstudio'],
    'C': ['embedded c', 'ansi c'],
    'Kotlin': ['kotlin'],
    'Swift': ['swift', 'swiftui'],
    'Next.js': ['next.js', 'nextjs'],
    'HTML/CSS': ['html5', 'css3', 'tailwind', 'sass', 'scss'],
    'Microservices': ['microservices', 'microservice'],
    'Linux administration': ['linux administration', 'bash', 'shell scripting'],
    'Ansible': ['ansible'],
    'Helm': ['helm'],
    'Prometheus': ['prometheus', 'grafana', 'datadog', 'observability'],
    'Cybersecurity': ['cybersecurity', 'information security', 'informationssicherheit', 'iso 27001', 'siem', 'soc', 'penetration testing'],
    'Controlling': ['controlling', 'budgeting', 'forecasting', 'variance analysis', 'abweichungsanalyse', 'kostenrechnung'],
    'IFRS': ['ifrs', 'us-gaap', 'hgb'],
    'Tax': ['tax law', 'steuerrecht', 'umsatzsteuer', 'vat', 'steuererklärung', 'tax compliance'],
    'Audit': ['internal audit', 'wirtschaftsprüfung', 'revision', 'sox'],
    'M&A': ['m&a', 'due diligence', 'valuation', 'unternehmensbewertung'],
    'Contract law': ['contract law', 'vertragsrecht', 'vertragsprüfung', 'contract negotiation', 'vertragsverhandlung'],
    'GDPR': ['gdpr', 'dsgvo', 'data protection', 'datenschutz', 'privacy compliance'],
    'Employment law': ['employment law', 'labour law', 'labor law', 'arbeitsrecht', 'betriebsrat', 'works council'],
    'Compliance': ['compliance', 'regulatory affairs', 'regulatory compliance', 'risk management', 'risikomanagement'],
    'HR business partnering': ['hr business partner', 'hrbp', 'personalbetreuung', 'employee relations'],
    'Employer branding': ['employer branding', 'talent acquisition', 'active sourcing'],
    'Performance marketing': ['performance marketing', 'google ads', 'sea', 'paid social', 'meta ads', 'paid search'],
    'Content marketing': ['content marketing', 'copywriting', 'editorial', 'redaktion'],
    'CRM': ['crm', 'crm management', 'customer relationship management'],
    'B2B sales': ['b2b sales', 'b2b-vertrieb', 'enterprise sales', 'new business', 'neukundenakquise', 'cold calling', 'kaltakquise'],
    'Account management': ['account management', 'key account', 'kundenbindung', 'bestandskunden'],
    'Supply chain': ['supply chain', 'lieferkette', 'procurement', 'einkauf', 'beschaffung', 'disposition', 'inventory management', 'bestandsmanagement'],
    'ERP': ['erp', 'sap s/4hana', 'sap mm', 'sap sd', 'sap fi', 'sap co', 'microsoft dynamics', 'navision'],
    'Quality management': ['quality management', 'qualitätsmanagement', 'iso 9001', 'six sigma', 'lean management', 'kaizen', 'capa'],
    'GMP': ['gmp', 'cgmp', 'clinical trials', 'klinische studien', 'pharmacovigilance', 'regulatory submissions'],
    'PLC': ['plc', 'sps', 'siemens s7', 'tia portal', 'scada', 'automation engineering', 'automatisierungstechnik'],
    'Embedded systems': ['embedded systems', 'embedded software', 'firmware', 'rtos', 'microcontroller', 'can bus'],
    'Mechanical engineering': ['mechanical engineering', 'maschinenbau', 'fem', 'fea', 'ansys', 'catia', 'creo', 'inventor', 'nx'],
    'BIM': ['bim', 'revit', 'civil engineering', 'bauingenieurwesen', 'bauleitung', 'hoai', 'vob'],
    'Clinical care': ['patient care', 'patientenversorgung', 'krankenpflege', 'intensivpflege', 'clinical practice', 'diagnose', 'therapie'],
    'Laboratory': ['laboratory', 'laborarbeit', 'hplc', 'pcr', 'elisa', 'cell culture', 'zellkultur', 'assay development'],
    'Event management': ['event management', 'veranstaltungsmanagement', 'veranstaltungsplanung'],
    'Office management': ['office management', 'büromanagement', 'sekretariat', 'correspondence', 'korrespondenz'],
    'Change management': ['change management', 'veränderungsmanagement', 'organisationsentwicklung', 'organizational development'],
    'Workshop facilitation': ['workshop facilitation', 'moderation', 'facilitation', 'workshops moderieren'],
}

ALL_SKILLS: dict[str, list[str]] = {
    **SKILLS,
    **{k: v for k, v in EXTRA_SKILLS.items() if k not in SKILLS},
}
_SKILL_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    skill: [_pattern(alias) for alias in aliases]
    for skill, aliases in ALL_SKILLS.items()
}
_SKILL_COMBINED: dict[str, re.Pattern[str]] = {
    skill: re.compile('|'.join(p.pattern for p in pats), re.I)
    for skill, pats in _SKILL_PATTERNS.items()
}

# Python's case-insensitive regex also matches these Unicode variants of i and s.
_SKILL_CASE_TRANSLATION = str.maketrans({'İ': 'i', 'ı': 'i', 'ſ': 's'})
_SKILL_LITERALS = {
    skill: tuple(alias.translate(_SKILL_CASE_TRANSLATION).lower() for alias in aliases)
    for skill, aliases in ALL_SKILLS.items()
}


# Skills that frequently appear as incidental/generic tools across unrelated professions.
GENERIC_SKILLS: frozenset[str] = frozenset({
    'Excel',
    'Slack',
    'Notion',
    'Jira',
    'Confluence',
    'Trello',
    'Miro',
    'Project management',
    'Project coordination',
    'Stakeholder coordination',
    'Process improvement',
    'KPI monitoring',
    'Customer service',
    'Onboarding',
    'Workshop facilitation',
    'Agile',
    'OKR',
})

# ---------------------------------------------------------------------------
# Expanded bilingual role groups (built on top of v1 ROLE_GROUPS)
# ---------------------------------------------------------------------------

EXTRA_ROLE_GROUPS: tuple[tuple[str, ...], ...] = (
    ('learning and development', 'l&d', 'l&d manager', 'l&d specialist', 'l&d coordinator',
     'training manager', 'training specialist', 'training coordinator', 'instructional designer',
     'e-learning specialist', 'e-learning manager', 'personalentwicklung', 'personalentwickler',
     'personalentwicklerin', 'weiterbildungsmanager', 'weiterbildungskoordinator',
     'bildungskoordinator', 'bildungsreferent', 'bildungsreferentin', 'lernbegleiter',
     'enablement manager', 'sales enablement', 'people development'),
    ('hr manager', 'hr business partner', 'hr generalist', 'people partner', 'people operations',
     'human resources', 'personalreferent', 'personalreferentin', 'personalsachbearbeiter',
     'recruiter', 'talent acquisition', 'personalberater', 'people & culture'),
    ('legal counsel', 'in-house counsel', 'corporate counsel', 'lawyer', 'attorney', 'paralegal',
     'syndikusrechtsanwalt', 'syndikusrechtsanwältin', 'rechtsanwalt', 'rechtsanwältin',
     'volljurist', 'volljuristin', 'jurist', 'juristin', 'rechtsreferent', 'compliance officer',
     'compliance manager', 'datenschutzbeauftragter', 'privacy counsel'),
    ('controller', 'financial controller', 'business controller', 'fp&a', 'finance manager',
     'treasury', 'tax manager', 'steuerberater', 'steuerfachangestellte', 'steuerfachwirt',
     'wirtschaftsprüfer', 'auditor', 'revisor', 'buchhalter', 'bilanzbuchhalter', 'finanzbuchhalter'),
    ('ux designer', 'ui designer', 'product designer', 'interaction designer', 'ux researcher',
     'visual designer', 'graphic designer', 'grafikdesigner', 'mediengestalter', 'brand designer'),
    ('customer success', 'customer success manager', 'client success', 'onboarding manager',
     'technical account manager', 'support engineer', 'customer support', 'kundenbetreuer',
     'kundenbetreuerin', 'service desk'),
    ('supply chain manager', 'procurement manager', 'buyer', 'purchasing', 'einkäufer', 'einkäuferin',
     'disponent', 'disponentin', 'operations manager', 'betriebsleiter', 'logistikleiter',
     'warehouse manager', 'lagerleiter', 'fleet manager'),
    ('mechanical engineer', 'maschinenbauingenieur', 'konstruktionsingenieur', 'fertigungsingenieur',
     'manufacturing engineer', 'industrial engineer', 'wirtschaftsingenieur', 'verfahrensingenieur',
     'process engineer', 'qualitätsingenieur', 'quality engineer', 'testing engineer', 'versuchsingenieur'),
    ('electrical engineer', 'elektroingenieur', 'hardware engineer', 'embedded engineer',
     'automation engineer', 'automatisierungsingenieur', 'sps-programmierer', 'mechatroniker',
     'elektroniker', 'servicingenieur', 'field service engineer'),
    ('civil engineer', 'bauingenieur', 'bauleiter', 'site manager', 'architekt', 'architect',
     'projektentwickler immobilien', 'facility manager', 'gebäudetechnik', 'tga-ingenieur'),
    ('physician', 'doctor', 'arzt', 'ärztin', 'facharzt', 'apotheker', 'pharmacist',
     'medical science liaison', 'clinical research associate', 'cra', 'biologist', 'chemist',
     'chemiker', 'laborant', 'cta', 'bta', 'mta', 'physiotherapeut', 'ergotherapeut',
     'krankenpfleger', 'gesundheits- und krankenpfleger', 'altenpfleger'),
    ('teacher', 'lehrer', 'lehrerin', 'dozent', 'dozentin', 'erzieher', 'erzieherin',
     'pädagoge', 'pädagogin', 'sozialpädagoge', 'sozialarbeiter', 'social worker'),
    ('marketing manager', 'growth manager', 'performance marketing', 'online marketing',
     'digital marketing', 'brand manager', 'communications manager', 'pr manager',
     'unternehmenskommunikation', 'pressesprecher', 'social media manager', 'content manager'),
    ('sales manager', 'business development', 'account executive', 'sales representative',
     'key account manager', 'vertriebsmitarbeiter', 'vertriebsleiter', 'vertriebsingenieur',
     'außendienst', 'innendienst', 'sdr', 'bdr'),
    ('data engineer', 'analytics engineer', 'bi developer', 'bi analyst', 'business intelligence',
     'ml engineer', 'machine learning engineer', 'ai engineer', 'research scientist', 'nlp engineer'),
    ('security engineer', 'information security', 'cybersecurity', 'it security', 'it-sicherheit',
     'soc analyst', 'pentester', 'network engineer', 'netzwerkadministrator', 'it administrator',
     'it support', 'system engineer', 'solutions architect', 'enterprise architect'),
    ('qa engineer', 'test engineer', 'quality assurance', 'software tester', 'test manager',
     'testautomatisierung', 'automation tester'),
    ('scrum master', 'agile coach', 'release train engineer', 'delivery manager'),
)

ALL_ROLE_GROUPS: tuple[tuple[str, ...], ...] = ROLE_GROUPS + EXTRA_ROLE_GROUPS

# ---------------------------------------------------------------------------
# General bilingual professional discipline taxonomy (~22 general families)
# ---------------------------------------------------------------------------

# Each discipline has (title_pattern, body_pattern, is_specialist).
# Specialist disciplines require explicit profile evidence; generic overlap does
# not substitute for specialist domain competence.
DISCIPLINES: dict[str, dict[str, Any]] = {
    'software_engineering': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:software\s*(?:engineer|developer|architect|entwickler\w*)|'
            r'(?:backend|back-end|frontend|front-end|full[- ]?stack|mobile|ios|android|web|embedded|firmware)\s*'
            r'(?:engineer|developer|entwickler\w*)|'
            r'(?:python|java|javascript|typescript|c\+\+|c#|rust|golang|php|ruby|kotlin|swift|\.net)\s*'
            r'(?:engineer|developer|entwickler\w*)|'
            r'programmierer\w*|anwendungsentwickler\w*|qa\s+engineer|test\s+automation|software\s+tester)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:software\s+(?:development|engineering|architecture)|softwareentwicklung|'
            r'code\s+reviews?|pull\s+requests?|unit\s+tests?|integration\s+tests?|'
            r'microservices?|rest\s+apis?|graphql|backend\s+services?|frontend\s+components?|'
            r'ci/cd\s+pipelines?|object-oriented|clean\s+code|system\s+architecture|'
            r'python|typescript|javascript|java|c\+\+|c#|golang|rust|react|angular|vue|django|fastapi|spring\s+boot)\b',
            re.I,
        ),
    },
    'devops_cloud': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:devops|sre|site\s+reliability|platform\s+engineer|cloud\s+(?:engineer|architect)|'
            r'infrastructure\s+engineer|system\s*administrator\w*|sysadmin|it[- ]?administrator\w*|'
            r'it[- ]?support\w*|it[- ]?helpdesk|fachinformatiker\w*|'
            r'network\s+engineer|netzwerkadministrator\w*|security\s+engineer|cybersecurity|it[- ]?sicherheit)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:kubernetes|k8s|docker|terraform|ansible|helm|aws|azure|gcp|google\s+cloud|'
            r'infrastructure\s+as\s+code|linux\s+(?:server|administration)|prometheus|grafana|'
            r'ci/cd|network\s+security|firewalls?|active\s+directory|penetration\s+testing|siem|iso\s*27001|'
            r'1st[- ]?level|2nd[- ]?level|first[- ]?level|second[- ]?level|ticketing|hardware[- ]?und\s+software)\b',
            re.I,
        ),
    },
    'data_ml': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:data\s+(?:scientist|engineer|analyst)|datenanalyst\w*|analytics\s+engineer|'
            r'machine\s+learning|ml\s+engineer|ai\s+engineer|bi\s+(?:developer|analyst|engineer)|'
            r'business\s+intelligence|statistician|statistiker\w*|quantitative\s+analyst)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:machine\s+learning|maschinelles\s+lernen|deep\s+learning|pytorch|tensorflow|'
            r'scikit-learn|data\s+(?:pipelines?|warehouse|modeling|modelling|science)|'
            r'sql\s+queries|bigquery|snowflake|airflow|dbt|pyspark|apache\s+spark|'
            r'statistical\s+analysis|predictive\s+model\w*|power\s+bi|tableau|datenanalyse|etl)\b',
            re.I,
        ),
    },
    'product_management': {
        'specialist': False,
        'title': re.compile(
            r'\b(?:product\s+(?:manager|owner|lead|director|specialist|operations|strategist)|'
            r'produktmanager\w*|produktentwicklung|produktentwickler\w*|produktmanagement)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:product\s+(?:roadmap|backlog|strategy|discovery|lifecycle|vision)|'
            r'produktstrategie|produkt-?roadmap|user\s+stories|feature\s+priorit\w*|'
            r'sprint\s+(?:planning|reviews?)|go-to-market|product\s+owner|produktentwicklung|'
            r'requirements?\s+(?:engineering|gathering|analysis)|anforderungsmanagement)\b',
            re.I,
        ),
    },
    'project_operations': {
        'specialist': False,
        'title': re.compile(
            r'\b(?:project\s+(?:manager|coordinator|lead|officer|assistant|delivery)|'
            r'program(?:me)?\s+(?:manager|coordinator|lead|assistant)|pmo|'
            r'projektmanager\w*|projektkoordinator\w*|projektleiter\w*|projektleitung|'
            r'programmkoordinator\w*|operations\s+(?:manager|coordinator|specialist)|'
            r'business\s+(?:analyst|operations)|process\s+(?:manager|improvement)|prozessmanager\w*|'
            r'scrum\s+master|agile\s+coach|change\s+manager)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:project\s+(?:planning|coordination|management|delivery|tracking)|'
            r'projekt(?:planung|koordination|management|steuerung|umsetzung)|'
            r'milestones?|meilenstein\w*|stakeholder\s+(?:alignment|coordination|management)|'
            r'process\s+(?:improvement|optimi[sz]ation|mapping)|prozessoptimierung|'
            r'cross-functional|abteilungsübergreifend|program(?:me)?\s+coordination|change\s+management)\b',
            re.I,
        ),
    },
    'hr_people_ld': {
        'specialist': False,
        'title': re.compile(
            r'\b(?:human\s+resources|hr[- ]?(?:manager|business\s+partner|generalist|specialist|coordinator|consultant|sachbearbeiter\w*|assist\w*|admin\w*)|'
            r'people\s+(?:partner|operations|development|&?\s*culture)|'
            r'recruiter\w*|recruiting|talent\s+acquisition|personalreferent\w*|personalentwickl\w*|personalsachbearbeiter\w*|'
            r'learning\s+(?:&|and)\s+development|l&d\b|training\s+(?:manager|coordinator|specialist)|'
            r'instructional\s+designer|e-learning|weiterbildung\w*|schulungskoordinator\w*|'
            r'bildungsreferent\w*|enablement\s+manager)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:learning\s+(?:&|and)\s+development|personalentwicklung|weiterbildung|'
            r'training\s+(?:programs?|delivery|solutions?|needs|concept)|schulungskonzept\w*|'
            r'instructional\s+design|didaktik|e-learning|lms|learning\s+management\s+system|'
            r'onboarding\s+(?:program|process)|talent\s+(?:acquisition|development|management)|'
            r'recruiting|bewerbermanagement|personalbeschaffung|hr\s+business\s+partner|'
            r'employee\s+(?:development|engagement|relations)|mitarbeiterentwicklung)\b',
            re.I,
        ),
    },
    'finance_accounting': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:accountant|accounting|bookkeeper|buchhalter\w*|bilanzbuchhalter\w*|'
            r'finanzbuchhalter\w*|controller\w*|controlling|fp&a|finance\s+(?:manager|director|analyst)|'
            r'treasury|tax\s+(?:manager|advisor|consultant|specialist)|steuerberater\w*|'
            r'steuerfachangestellt\w*|wirtschaftsprüfer\w*|auditor\w*|revisor\w*|payroll|lohnbuchhalt\w*)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:financial\s+(?:accounting|reporting|statements?|closing|audit)|'
            r'general\s+ledger|accounts\s+(?:payable|receivable)|kreditoren|debitoren|'
            r'finanzbuchhaltung|buchführung|jahresabschluss|monatsabschluss|bilanzierung|'
            r'\bhgb\b|\bifrs\b|\bdatev\b|kostenrechnung|abweichungsanalyse|lohnabrechnung|'
            r'steuererklärung|umsatzsteuer|wirtschaftsprüfung|rechnungsprüfung|cash\s+flow\s+forecasting)\b',
            re.I,
        ),
    },
    'legal_compliance': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:legal\s+(?:counsel|advisor|manager|specialist|operations)|'
            r'(?:in-house|corporate|general|privacy|employment)\s+counsel|'
            r'lawyer|attorney|solicitor|paralegal|syndikus\w*|rechtsanwalt|rechtsanwältin|'
            r'volljurist\w*|jurist(?:in)?\b|rechtsreferent\w*|compliance\s+(?:officer|manager)|'
            r'datenschutzbeauftragte\w*|notarfachangestellt\w*|rechtsanwaltsfachangestellt\w*)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:legal\s+(?:advice|counsel|review|opinions?|disputes?|proceedings?|representation)|'
            r'rechtsberatung|rechtsprüfung|vertragsrecht|vertragsprüfung|gesellschaftsrecht|'
            r'arbeitsrecht|staatsexamen|befähigung\s+zum\s+richteramt|zulassung\s+als\s+rechtsanwalt|'
            r'litigation|prozessführung|regulatory\s+compliance|gdpr|dsgvo|datenschutzrecht|'
            r'draft(?:ing)?\s+(?:and\s+negotiat\w*\s+)?contracts?|legal\s+entity|aufsichtsbehörden)\b',
            re.I,
        ),
    },
    'sales_bd': {
        'specialist': False,
        'title': re.compile(
            r'\b(?:sales\s+(?:manager|representative|executive|director|engineer|development|lead)|'
            r'account\s+(?:executive|manager)|key\s+account|business\s+development|'
            r'\bsdr\b|\bbdr\b|vertrieb\w*|außendienst\w*|kundenberater\w*)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:sales\s+(?:pipeline|targets?|quota|cycle|funnel)|vertriebsziele|'
            r'cold\s+calling|kaltakquise|neukundenakquise|new\s+business\s+development|'
            r'closing\s+deals|contract\s+closing|vertragsabschluss|lead\s+generation|'
            r'upselling|cross-selling|key\s+account\s+management|angebotserstellung|salesforce\s+crm)\b',
            re.I,
        ),
    },
    'marketing_growth': {
        'specialist': False,
        'title': re.compile(
            r'\b(?:marketing\s+(?:manager|specialist|coordinator|director|lead)|'
            r'growth\s+(?:manager|marketer)|performance\s+marketing|online\s+marketing|'
            r'digital\s+marketing|brand\s+manager|seo\s+(?:manager|specialist)|'
            r'content\s+(?:manager|marketing|strategist)|social\s+media\s+manager|'
            r'communications\s+manager|pr\s+manager|unternehmenskommunikation|pressesprecher\w*)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:marketing\s+(?:campaigns?|strategy|channels?|funnel)|marketingkampagnen|'
            r'performance\s+marketing|google\s+ads|paid\s+(?:social|search)|meta\s+ads|'
            r'\bseo\b|\bsea\b|suchmaschinenoptimierung|content\s+(?:marketing|creation|calendar)|'
            r'brand\s+(?:awareness|positioning)|social\s+media\s+channels?|public\s+relations|'
            r'press\s+releases?|pressemitteilungen|conversion\s+rate\s+optimi[sz]ation)\b',
            re.I,
        ),
    },
    'design_ux': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:ux\s*(?:designer|researcher|lead)|ui\s*(?:designer|lead)|ui/ux|'
            r'product\s+designer|interaction\s+designer|visual\s+designer|graphic\s+designer|'
            r'grafikdesigner\w*|mediengestalter\w*|brand\s+designer|industrial\s+designer|'
            r'art\s+director|creative\s+director)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:figma|sketch|adobe\s+(?:xd|illustrator|photoshop|indesign|creative\s+cloud)|'
            r'design\s+systems?|wireframes?|interactive\s+prototypes?|user\s+(?:interface|flows?|research|testing)|'
            r'usability\s+testing|visual\s+design|typography|typografie|layoutgestaltung|branding\s+guidelines)\b',
            re.I,
        ),
    },
    'customer_support_success': {
        'specialist': False,
        'title': re.compile(
            r'\b(?:customer\s+(?:service|support|success|care|operations)|'
            r'client\s+(?:service|support|success)|kundenservice|kundenbetreuung|kundenbetreuer\w*|'
            r'kundensupport|service\s+desk|helpdesk|support\s+(?:specialist|agent|representative))\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:customer\s+(?:inquiries|tickets|support|retention|onboarding|satisfaction)|'
            r'kundenanfragen|support\s+tickets?|zendesk|intercom|service\s+level\s+agreement|\bsla\b|'
            r'call\s+center|kundenbetreuung|beschwerdemanagement|complaint\s+handling|churn\s+reduction)\b',
            re.I,
        ),
    },
    'healthcare_clinical': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:nurse|nursing|pflegefachkraft|pflegekraft|krankenpfleger\w*|altenpfleger\w*|'
            r'gesundheits-\s*und\s+krankenpfleg\w*|physician|doctor|arzt|ärztin|facharzt|fachärztin|'
            r'pharmacist|apotheker\w*|therapist|physiotherapeut\w*|ergotherapeut\w*|logopäde\w*|'
            r'midwife|hebamme|dentist|zahnarzt|zahnärztin|medical\s+assistant|medizinische\s+fachangestellte|'
            r'paramedic|notfallsanitäter\w*|psychotherapeut\w*|klinische\s+psycholog\w*)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:patient\s+(?:care|treatment|safety|monitoring)|patientenversorgung|krankenpflege|'
            r'intensivpflege|pflegeplanung|pflegedokumentation|approbation|pflegeexamen|'
            r'clinical\s+(?:diagnosis|treatment|rounds|ward)|stationsleitung|krankenhaus|'
            r'ärztliche\s+versorgung|medikamentengabe|heilmittel|therapieplan)\b',
            re.I,
        ),
    },
    'pharma_biotech': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:clinical\s+research|pharmacovigilance|regulatory\s+affairs|medical\s+science\s+liaison|'
            r'biologist|biologe|biologin|chemist|chemiker\w*|biochemist|microbiologist|'
            r'lab(?:oratory)?\s+(?:technician|manager|scientist)|laborant\w*|chemielaborant\w*|'
            r'biologielaborant\w*|\bcta\b|\bbta\b|\bpta\b|pharma(?:zeut\w*|referent\w*)|'
            r'validation\s+engineer|gmp\s+(?:manager|specialist|qa))\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:\bgmp\b|\bcgmp\b|\bgcp\b|\bglp\b|pharmacovigilance|clinical\s+trials?|klinische\s+studien|'
            r'\bhplc\b|\bpcr\b|\belisa\b|cell\s+culture|zellkultur|assay\s+development|laboranalytik|'
            r'arzneimittelzulassung|regulatory\s+submissions?|ema|fda\s+compliance|cleanroom|reinraum|'
            r'qualifizierung\s+und\s+validierung|\biq/oq/pq\b)\b',
            re.I,
        ),
    },
    'mechanical_hardware': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:mechanical\s+engineer|maschinenbauingenieur\w*|konstrukteur\w*|cad\s+(?:designer|engineer)|'
            r'manufacturing\s+engineer|fertigungsingenieur\w*|industrial\s+engineer|production\s+engineer|'
            r'produktionsingenieur\w*|process\s+engineer|verfahrensingenieur\w*|'
            r'quality\s+engineer|qualitätsingenieur\w*|versuchsingenieur\w*|'
            r'(?:product\s+|advanced\s+)?development\s+engineer|entwicklungsingenieur\w*|'
            r'innovation\s+engineer|innovationsingenieur\w*|prototyping\s+engineer|test\s+engineer|'
            r'mechatroniker\w*|industriemechaniker\w*|zerspanungsmechaniker\w*|werkzeugmechaniker\w*)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:solidworks|autocad|catia|creo|siemens\s+nx|inventor|fusion\s+360|'
            r'technical\s+drawings?|technische\s+zeichnungen|bill\s+of\s+materials|stückliste|'
            r'maschinenbau|mechanical\s+engineering|mechatronics|mechatronik|konstruktion|3d[- ]?cad|'
            r'fem\s+simulation|cnc|fertigungstechnik|manufacturing|production\s+line|serienfertigung|'
            r'spritzguss|injection\s+molding|prototyp(?:e|es|ing)|funktionsmuster\w*|prototypenbau|'
            r'3d[- ]?druck|3d\s+printing|serienreife|fixtures?|test\s+rigs?|versuchsaufbau\w*|versuchsdokumentation|'
            r'fmea|iso\s*9001|iatf\s*16949|lean\s+manufacturing|werkstofftechnik)\b',
            re.I,
        ),
    },
    'electrical_embedded': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:electrical\s+engineer|elektroingenieur\w*|elektroniker\w*|elektriker\w*|'
            r'hardware\s+engineer|pcb\s+designer|automation\s+engineer|automatisierungsingenieur\w*|'
            r'sps[- ]?programmierer\w*|controls\s+engineer|steuerungstechnik|'
            r'energietechnik|hochspannungstechnik|leistungselektronik|field\s+service\s+technician)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:elektrotechnik|electrical\s+engineering|schaltpläne|circuit\s+design|pcb\s+layout|'
            r'altium|eagle|eplan|\bsps\b|\bplc\b|siemens\s+s7|tia\s+portal|scada|'
            r'microcontroller|embedded\s+c|can\s+bus|oscilloscope|oszilloskop|'
            r'schaltschrankbau|vde[- ]?norm|hochspannung|leistungselektronik|antriebstechnik)\b',
            re.I,
        ),
    },
    'civil_construction': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:civil\s+engineer|bauingenieur\w*|bauleiter\w*|oberbauleiter\w*|'
            r'architect|architekt\w*|statiker\w*|structural\s+engineer|tga[- ]?ingenieur\w*|'
            r'versorgungsingenieur\w*|construction\s+site\s+manager|baustellenleiter\w*|'
            r'zimmerer|maurer|dachdecker|anlagenmechaniker\s+shk|kälteanlagenbauer)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:bauleitung|baustellen|bauüberwachung|bauingenieurwesen|hochbau|tiefbau|'
            r'\bhoai\b|\bvob\b|\bbim\b|revit|archicad|tragwerksplanung|statik|bauantrag|'
            r'genehmigungsplanung|ausführungsplanung|leistungsverzeichnis|ausschreibung\s+und\s+vergabe|'
            r'gebäudetechnik|heizung[,\s]+lüftung|sanitär)\b',
            re.I,
        ),
    },
    'logistics_supply_chain': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:logistics\s+(?:team\s+|shift\s+)?(?:manager|coordinator|specialist|lead(?:er)?|planner|engineer|director)|logistik\w*|'
            r'supply\s+chain\s+(?:manager|planner|analyst|coordinator|lead(?:er)?|specialist)|'
            r'procurement\s+(?:manager|specialist|lead(?:er)?)|buyer|purchasing|einkäufer\w*|'
            r'disponent\w*|dispatcher|freight\s+(?:manager|coordinator|forwarder)|spedition\w*|'
            r'transport(?:ation)?\s+(?:manager|planner|coordinator)|distribution\s+(?:manager|center|lead(?:er)?)|'
            r'fleet\s+manager|fuhrparkleiter\w*|fulfillment\s+(?:manager|lead(?:er)?)|'
            r'(?:warehouse|shipping|picking|order\s+picking)\s+(?:team\s+|shift\s+)?(?:manager|lead(?:er)?|supervisor|worker|operator)|'
            r'(?:team|schicht)leiter\w*\s+(?:warehouse|lager|versand|logistik|kommissionierung|paketumschlag|spedition)|'
            r'versandleiter\w*|lagerleiter\w*|lagerist\w*|lagermitarbeiter\w*|'
            r'fachkraft\s+für\s+lagerlogistik|kommissionierer\w*|staplerfahrer\w*|'
            r'berufskraftfahrer\w*|lkw[- ]?fahrer\w*)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:supply\s+chain|lieferkette|wareneingang|warenausgang|inbound|outbound|'
            r'kommissionierung|picking|packing|fulfillment|distribution\s+center|paketumschlag|'
            r'lagerverwaltung|lagerhaltung|warehouse\s+(?:management|operations)|warehousing|\bwms\b|'
            r'gabelstapler|forklift|zollabwicklung|customs\s+(?:clearance|processing)|'
            r'freight\s+(?:forwarding|management|rates)|shipping|shipments?|carriers?|spedition|transportplanung|versand(?:abwicklung|steuerung)?|'
            r'route\s+planning|tourenplanung|fleet\s+management|fuhrpark|disposition|dispatching|'
            r'bestandsmanagement|inventory\s+(?:control|management|planning)|lieferantenbewertung|'
            r'beschaffung|procurement|einkaufsverhandlungen|sap\s+mm|sap\s+ewm|sap\s+tm)\b',
            re.I,
        ),
    },
    'education_social': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:teacher|lehrer\w*|lehrkraft|erzieher\w*|kindergarten|kita[- ]?leitung|'
            r'sozialpädagog\w*|sozialarbeiter\w*|social\s+worker|heilerziehungspfleger\w*|'
            r'sonderpädagog\w*|schulbegleiter\w*|professor\w*|postdoc)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:unterricht\w*|klassenleitung|lehrplan|curriculum\s+teaching|kinderbetreuung|'
            r'kindertagesstätte|frühpädagogik|sozialpädagogik|jugendhilfe|kinder-\s*und\s+jugendhilfe|'
            r'beratung\s+von\s+familien|sozialarbeit|staatliche\s+anerkennung\s+als\s+(?:erzieher|sozial))\b',
            re.I,
        ),
    },
    'hospitality_retail': {
        'specialist': True,
        'title': re.compile(
            r'\b(?:chef|koch|köchin|küchenchef|barkeeper|barista|waiter|waitress|kellner\w*|'
            r'servicekraft|hotelfach\w*|hotelmanager|rezeptionist\w*|housekeeping|'
            r'store\s+manager|filialleiter\w*|retail\s+(?:sales|associate|specialist|manager)|'
            r'(?:fach|einzelhandels|filial)?verkäufer\w*|einzelhandelskauf\w*|kassierer\w*|'
            r'visual\s+merchandiser|bäcker\w*|metzger\w*|friseur\w*)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:gastronomie|küchenbetrieb|speisenzubereitung|gästebetreuung|hotelbetrieb|'
            r'kassenabrechnung|warenpräsentation|einzelhandel|filialleitung|ladengeschäft|baumarkt|'
            r'verkaufsfläche|kundenberatung\s+im\s+(?:markt|geschäft|laden)|'
            r'schichtdienst\s+im\s+(?:hotel|restaurant|einzelhandel)|hospitality\s+operations)\b',
            re.I,
        ),
    },
    'administration_office': {
        'specialist': False,
        'title': re.compile(
            r'\b(?:executive\s+assistant|office\s+manager|administrative\s+assistant|'
            r'team\s+assistant|teamassistenz|assistenz\s+der\s+geschäfts(?:führung|leitung)|'
            r'sekretär\w*|bürokauf\w*|kaufmännische\w*\s+(?:mitarbeiter|angestellt|sachbearbeiter)|'
            r'sachbearbeiter\w*|empfangsmitarbeiter\w*|front\s+desk)\b',
            re.I,
        ),
        'body': re.compile(
            r'\b(?:office\s+(?:management|administration)|büroorganisation|büromanagement|'
            r'terminkoordination|reisekostenabrechnung|travel\s+bookings?|postbearbeitung|'
            r'korrespondenz|protokollführung|meeting\s+minutes|executive\s+support|'
            r'calendar\s+management|empfang\s+von\s+gästen)\b',
            re.I,
        ),
    },
}

# Soft adjacency map: when a profile has discipline A and job asks for B,
# partial domain alignment credit applies (symmetry maintained).
ADJACENT_DISCIPLINES: dict[str, set[str]] = {
    'software_engineering': {'devops_cloud', 'data_ml'},
    'devops_cloud': {'software_engineering', 'data_ml'},
    'data_ml': {'software_engineering', 'devops_cloud', 'product_management', 'finance_accounting'},
    'product_management': {'project_operations', 'design_ux', 'data_ml', 'marketing_growth', 'mechanical_hardware'},
    'project_operations': {'product_management', 'logistics_supply_chain', 'customer_support_success'},
    'hr_people_ld': {'administration_office', 'education_social'},
    'finance_accounting': {'legal_compliance', 'data_ml'},
    'legal_compliance': {'finance_accounting'},
    'sales_bd': {'marketing_growth', 'customer_support_success'},
    'marketing_growth': {'sales_bd', 'design_ux', 'product_management'},
    'design_ux': {'product_management', 'marketing_growth', 'software_engineering'},
    'customer_support_success': {'sales_bd', 'project_operations'},
    'healthcare_clinical': {'pharma_biotech'},
    'pharma_biotech': {'healthcare_clinical', 'mechanical_hardware'},
    'mechanical_hardware': {'electrical_embedded', 'product_management', 'civil_construction'},
    'electrical_embedded': {'mechanical_hardware', 'software_engineering'},
    'civil_construction': {'mechanical_hardware', 'project_operations'},
    'logistics_supply_chain': {'project_operations'},
    'education_social': {'hr_people_ld'},
    'hospitality_retail': {'customer_support_success', 'sales_bd'},
    'administration_office': {'hr_people_ld'},
}

# Specialist duty verbs from distinct regulated/technical professions (for domain_mismatch).
_SPECIALIST_DUTY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ('legal_compliance', re.compile(r'\b(?:represent\w*\s+in\s+court|legal\s+opinions?|rechtsgutachten|prozessführung|vertragsgestaltung|arbeitsrechtliche\s+beratung)\b', re.I)),
    ('finance_accounting', re.compile(r'\b(?:jahresabschluss\w*|bilanzierung|finanzbuchhaltung|general\s+ledger|steuererklärung|lohnabrechnung|wirtschaftsprüfung)\b', re.I)),
    ('healthcare_clinical', re.compile(r'\b(?:patientenversorgung|krankenpflege|diagnosestellung|medikamentengabe|clinical\s+care|intensivpflege)\b', re.I)),
    ('software_engineering', re.compile(r'\b(?:write\s+production\s+code|develop\s+backend|softwarearchitektur|implement\s+(?:microservices|apis)|entwickel\w*\s+software)\b', re.I)),
    ('mechanical_hardware', re.compile(r'\b(?:3d[- ]?konstruktion|cad[- ]?konstruktion|cnc[- ]?programmierung|maschinenkonstruktion|schaltplan\w*)\b', re.I)),
    ('civil_construction', re.compile(r'\b(?:bauüberwachung|baustellenleitung|tragwerksplanung|leistungsphasen\s+.*hoai)\b', re.I)),
)


# ---------------------------------------------------------------------------
# Substantive skill mention classification (ported & improved from Phase 1)
# ---------------------------------------------------------------------------

_INCIDENTAL_TOOL_RE = re.compile(
    r'\b(?:we\s+use|our\s+(?:stack|team|company)\s+(?:uses?|runs?|works?\s+with)|'
    r'such\s+as|for\s+example|e\.g\.|z\.b\.|wie\s+z\.?b\.?|'
    r'via|through|über|using\s+our|in\s+our|auf\s+unserer|'
    r'training\s+(?:provided|offered|on)|we\s+(?:train|teach)\s+you|schulung\s+wird\s+angeboten|'
    r'no\s+prior\s+experience\s+(?:in|with)\s+needed|keine\s+vorkenntnisse)\b',
    re.I,
)
_PASSIVE_CONTAINER_RE = re.compile(
    r'\b(?:documented\s+in|stored\s+in|tracked\s+in|hosted\s+on|communicated\s+via|'
    r'managed\s+in|maintained\s+in|shared\s+via|published\s+on|'
    r'dokumentiert\s+in|gepflegt\s+in|verwaltet\s+in|kommuniziert\s+über)\b',
    re.I,
)
_PASSIVE_SYSTEM_MODIFIER = re.compile(
    r'^\s*[-–]?\s*(?:based|basiert\w*|gestützt\w*|powered|driven|hosted)\s+'
    r'(?:[\w-]+\s+){0,2}(?:system\w*|tool\w*|portal\w*|plattform\w*|platform\w*|software|app\w*|'
    r'anwendung\w*|datenbank\w*|database\w*|lösung\w*|solution\w*)\b',
    re.I,
)
_EMPLOYER_CONTAINER_PREFIX = re.compile(
    r'\b(?:our|unser\w*)\s+(?:(?:internal|in-house|custom|company|hospital|warehouse|existing|modern|intern\w*|eigen\w*)\s+){0,2}$',
    re.I,
)
_EMPLOYER_CONTAINER_SUFFIX = re.compile(
    r'^\s+(?:system\w*|portal\w*|plattform\w*|platform\w*|tool\w*|app\w*|anwendung\w*|software|infrastructure|environment)\b',
    re.I,
)
_ACTIVE_TOOL_VERB = re.compile(
    r'\b(?:develop\w*|build\w*|built|write|writing|code|coding|program\w*|script\w*|query\w*|'
    r'architect\w*|engineer\w*|deploy\w*|implement\w*|maintain\w*|administer\w*|automate\w*|'
    r'configure\w*|debug\w*|entwickel\w*|entwicklung|erstell\w*|bau\w*|entwerf\w*|programmier\w*|'
    r'cod\w*|skript\w*|abfrag\w*|schreib\w*|pfleg\w*|wart\w*|betreib\w*|implementier\w*|'
    r'administrier\w*|automatisier\w*|konfigurier\w*)\b',
    re.I,
)
_TRAINING_OFFERED = re.compile(
    r'\b(?:(?:free|paid|complimentary|internal|optional|kostenlos\w*|bezahlt\w*|intern\w*)\s+'
    r'(?:[\w-]+\s+){0,3}(?:training|courses?|workshops?|schulung\w*|weiterbildung\w*|kurs\w*)|'
    r'(?:training|courses?|workshops?|schulung\w*|weiterbildung\w*)\s+(?:offered|provided|available|included|angeboten|möglich))\b',
    re.I,
)
_OTHER_ACTOR_RELATIVE = re.compile(
    r'\b(?:engineers?|developers?|programmers?|analysts?|specialists?|colleagues?|teams?|'
    r'it\s+team|data\s+team|entwickler\w*|ingenieur\w*|programmierer\w*|analyst\w*|spezialist\w*|'
    r'kolleg\w*|fachabteilung\w*|team\w*|it.team|datenteam)\s*(?:,\s*)?'
    r'(?:who|that|which|die|der|welche[rs]?)\b[^.;]{0,60}$',
    re.I,
)


def classify_skill_mention(sentence: str, start: int, end: int, scope: str) -> str:
    """Classify a skill mention as 'required', 'duty', 'preferred', or 'incidental'."""
    if is_source_instruction(sentence) or is_language_or_visa_clause(sentence):
        return 'incidental'
    if scope == 'benefits':
        return 'incidental'
    if NON_REQUIREMENT_V2.search(sentence) and not REQUIREMENT.search(sentence):
        return 'incidental'
    if other_actor(sentence):
        return 'incidental'

    before_full = sentence[:start]
    after_full = sentence[end:]
    before = sentence[max(0, start - 50):start]
    local = sentence[max(0, start - 50):min(len(sentence), end + 50)]
    if _OTHER_ACTOR_RELATIVE.search(before_full):
        return 'incidental'
    if _PASSIVE_CONTAINER_RE.search(before):
        return 'incidental'
    if scope != 'requirements' and not REQUIREMENT.search(sentence) and _TRAINING_OFFERED.search(sentence):
        return 'incidental'
    if re.search(
        r'\b(?:we\s+(?:will\s+)?(?:train|teach)|training\s+(?:is\s+)?(?:provided|offered)|'
        r'no\s+(?:prior\s+)?experience\s+(?:required|needed)|keine\s+vorkenntnisse)\b',
        local,
        re.I,
    ):
        return 'incidental'
    if not _ACTIVE_TOOL_VERB.search(sentence):
        if _PASSIVE_SYSTEM_MODIFIER.match(after_full[:60]):
            return 'incidental'
        if (
            scope != 'requirements'
            and not REQUIREMENT.search(sentence)
            and _EMPLOYER_CONTAINER_PREFIX.search(before)
            and _EMPLOYER_CONTAINER_SUFFIX.match(after_full[:40])
        ):
            return 'incidental'

    pol = requirement_polarity(sentence, start, end)
    if pol in {'waived', 'instruction'}:
        return 'incidental'
    if pol == 'optional' or scope == 'optional' or OPTIONAL.search(local):
        return 'preferred'
    if _INCIDENTAL_TOOL_RE.search(before) and scope not in {'requirements', 'duties'}:
        return 'incidental'
    if scope == 'requirements' or REQUIREMENT.search(sentence):
        return 'required'
    if scope == 'duties':
        return 'duty'
    return 'duty'


# ---------------------------------------------------------------------------
# Company-subject experience boast guard & OR-arm parsing
# ---------------------------------------------------------------------------

_COMPANY_EXPERIENCE_CLAIM = re.compile(
    r'\b(?:'
    r'(?:our|the)\s+(?:company|firm|team|organi[sz]ation|agency|group|brand|startup|business)\s+'
    r'(?:has|have|boasts?|brings?|celebrates?|looks?\s+back\s+on|with)\b|'
    r'(?:we|wir)\s+(?:have|haben|verfügen\s+über|blicken\s+auf|bringen)\b|'
    r'(?:unser\w*)\s+(?:unternehmen|firma|team|gruppe|agentur)\s+'
    r'(?:hat|haben|verfügt|blickt|besteht|existiert)\b|'
    r'(?:over|more\s+than|über|mehr\s+als)\s+\d{1,2}\s+(?:years?|jahre?n?)\s+'
    r'(?:of\s+)?(?:history|heritage|tradition|market\s+(?:presence|leadership)|in\s+business|'
    r'geschichte|tradition|markterfahrung|erfahrung\s+am\s+markt|bestehen)|'
    r'(?:founded|established|gegründet)\s+(?:in\s+\d{4}|over\s+\d{1,2}\s+years?\s+ago|vor\s+\d{1,2}\s+jahren)'
    r')\b',
    re.I,
)


def is_company_experience_claim(sentence: str) -> bool:
    """Return True when a tenure/years mention describes company history rather than applicant tenure."""
    return bool(_COMPANY_EXPERIENCE_CLAIM.search(sentence))


def split_or_arms(clause: str) -> list[str]:
    """Split a requirement clause into top-level OR alternatives."""
    if not clause:
        return []
    protected: dict[str, str] = {}
    working = clause
    for idx, m in reversed(list(enumerate(re.finditer(r'\([^()]*\)', working)))):
        marker = f'\ue000p{idx}\ue001'
        protected[marker] = m.group()
        working = working[:m.start()] + marker + working[m.end():]
    parts = [p.strip(' ,;.') for p in re.split(r'\s+\b(?:or|oder|alternatively|alternativ)\b\s+', working, flags=re.I) if p.strip(' ,;.')]
    if len(parts) <= 1:
        return [clause.strip()]
    out = []
    for part in parts:
        for marker, orig in protected.items():
            part = part.replace(marker, orig)
        out.append(part.strip())
    return out


# ---------------------------------------------------------------------------
# Seniority & tenure extraction (bilingual, OR-arm aware, company-boast safe)
# ---------------------------------------------------------------------------

_SENIORITY_LEVELS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (5, re.compile(r'\b(?:c-level|ceo|cto|cfo|coo|cpo|cmo|vp\b|vice\s+president|svp|evp|director|direktor\w*|head\s+of|bereichsleiter\w*|abteilungsleiter\w*|geschäftsführer\w*|vorstand)\b', re.I)),
    (4, re.compile(r'\b(?:principal|staff\s+(?:engineer|designer|product)|team\s+lead|tech\s+lead|teamleiter\w*|teamleitung|fachliche\s+leitung|lead\s+(?:engineer|developer|designer|product|manager|analyst|architect)|architect|architekt\w*)\b', re.I)),
    (3, re.compile(r'\b(?:senior|sr\.?\b|erfahrene[rnms]?\b|experienced\s+(?:engineer|developer|manager|specialist|consultant|analyst))\b', re.I)),
    (2, re.compile(r'\b(?:mid[- ]?level|professional|regular|specialist|manager|engineer|developer|analyst|coordinator|consultant|referent\w*|berater\w*|spezialist\w*)\b', re.I)),
    (1, re.compile(r'\b(?:junior|jr\.?\b|entry[- ]?level|graduate|trainee|berufseinsteiger\w*|einsteiger\w*|associate|assistant|assistent\w*|assistenz)\b', re.I)),
    (0, re.compile(r'\b(?:intern|internship|praktikant\w*|praktikum|working\s+student|werkstudent\w*|studentische\s+hilfskraft|auszubildend\w*|azubi|apprentice|dual\s+student|duales\s+studium)\b', re.I)),
)


def detect_title_seniority(title: str) -> int | None:
    """Detect seniority tier (0=intern .. 5=director/head) from a job or role title."""
    if not title:
        return None
    # Check extreme bands first (intern/working_student and director/lead/senior/junior),
    # falling back to mid-level (2) only if nothing stronger matches.
    for rank in (0, 5, 4, 3, 1, 2):
        for lv, pat in _SENIORITY_LEVELS:
            if lv == rank and pat.search(title):
                return lv
    return None


def _tenure_minimums(
    filtered_sentences: list[tuple[str, str, str]],
) -> list[tuple[float, str, str]]:
    """Extract stated tenure minima from requirements in the scoring evidence."""
    minimums = []
    for source, sentence, scope in filtered_sentences:
        if scope not in {'neutral', 'requirements'} or not EXPERIENCE_PATTERN.search(sentence):
            continue
        if is_source_instruction(sentence) or is_company_experience_claim(sentence):
            continue
        if NON_REQUIREMENT_V2.search(sentence) and not REQUIREMENT.search(sentence):
            continue
        if OPTIONAL.search(sentence) and not REQUIREMENT.search(sentence):
            continue

        arms = split_or_arms(sentence)
        arm_years: list[float] = []
        has_non_years_alternative = False
        for arm in arms:
            matches = [m for m in EXPERIENCE_PATTERN.finditer(arm) if _is_experience_match(m)]
            if not matches:
                if len(arms) > 1 and (
                    DEGREE.search(arm)
                    or re.search(r'\b(?:equivalent|comparable|vergleichbar|ausbildung|studium|degree|quereinsteiger)\b', arm, re.I)
                ):
                    has_non_years_alternative = True
                continue
            yrs_list = [y for m in matches if (y := _experience_years(m)) is not None and 0.5 <= y <= 20]
            if not yrs_list:
                continue
            min_arm = min(yrs_list)
            first = matches[0]
            has_explicit_min = bool(
                first.group('range_max')
                or first.group('marker')
                or first.group('reverse_marker')
                or first.group('plus')
                or REQUIREMENT.search(arm)
            )
            if scope == 'requirements' or (scope == 'neutral' and has_explicit_min):
                if requirement_polarity(arm, first.start(), first.end()) == 'required':
                    arm_years.append(min_arm)

        if not arm_years:
            continue
        effective = min(arm_years)
        if has_non_years_alternative:
            effective = max(0.0, effective - 1.5)
        minimums.append((effective, sentence, source))
    return minimums


def _extract_required_years_from_sentences(
    filtered_sentences: list[tuple[str, str, str]],
) -> tuple[float | None, str, str]:
    years, quote, source = max(_tenure_minimums(filtered_sentences), key=lambda row: row[0], default=(None, '', ''))
    return years, quote[:240], source


def _extract_required_years(texts: list[tuple[str, str]]) -> tuple[float | None, str]:
    """Extract required minimum years of experience, respecting OR-arms and ignoring company boasts."""
    filtered: list[tuple[str, str, str]] = []
    for source, text in texts:
        if source == 'title' or not text:
            continue
        init_scope = structured_source_scope(source)
        for sentence, scope in scoped_sentences(structured_text(text), initial_scope=init_scope):
            cleaned = strip_language_or_visa_subclauses(sentence)
            if cleaned:
                filtered.append((source, cleaned, scope))
    years, quote, _ = _extract_required_years_from_sentences(filtered)
    return years, quote


def _profile_experience_titles(profile: dict) -> list[str]:
    """Collect demonstrated past role titles from experience containers (never desired_roles)."""
    titles: list[str] = []
    seen: set[str] = set()

    def _add(val: Any) -> None:
        s = str(val or '').strip()
        if s and s.casefold() not in seen:
            seen.add(s.casefold())
            titles.append(s)

    for entry in (profile.get('experience_entries') or []):
        if isinstance(entry, dict):
            _add(entry.get('title') or entry.get('role'))
    app_details = profile.get('application_details') or {}
    if isinstance(app_details, dict):
        for entry in (app_details.get('experiences') or []):
            if isinstance(entry, dict):
                _add(entry.get('title') or entry.get('role'))
    facts = profile.get('cv_career_facts') or {}
    if isinstance(facts, dict):
        for entry in (facts.get('experiences') or []):
            if isinstance(entry, dict):
                _add(entry.get('title') or entry.get('role'))
    return titles


def _profile_seniority_and_years(profile: dict) -> tuple[int, float | None, str]:
    """Infer profile seniority level (0..5) and experience years from demonstrated profile facts."""
    years = profile.get('experience_years')
    if years is None:
        facts = profile.get('cv_career_facts') or {}
        if isinstance(facts, dict) and isinstance(facts.get('experience_years'), (int, float)):
            years = float(facts['experience_years'])
    else:
        years = float(years)

    explicit_sen = str(profile.get('seniority') or '').strip()
    sen_lv = detect_title_seniority(explicit_sen)
    if sen_lv is None:
        exp_lvs = [
            lv
            for title in _profile_experience_titles(profile)
            if (lv := detect_title_seniority(title)) is not None
        ]
        if exp_lvs:
            sen_lv = max(exp_lvs)
    if sen_lv is None and years is not None:
        if years < 1.0:
            sen_lv = 1
        elif years < 4.5:
            sen_lv = 2
        elif years < 8.5:
            sen_lv = 3
        else:
            sen_lv = 4
    if sen_lv is None:
        sen_lv = 2

    quote_parts = []
    if explicit_sen:
        quote_parts.append(f'seniority={explicit_sen}')
    if years is not None:
        quote_parts.append(f'experience_years={years:g}')
    return sen_lv, years, '; '.join(quote_parts)


# ---------------------------------------------------------------------------
# Discipline detection helpers
# ---------------------------------------------------------------------------

def _detect_disciplines(title: str, body_texts: list[str]) -> tuple[set[str], set[str], dict[str, int]]:
    """Return (title_disciplines, body_disciplines, hit_counts) from title + filtered body text."""
    title_hits: set[str] = set()
    body_hits: set[str] = set()
    counts: dict[str, int] = {}

    joined_body = '\n'.join(body_texts)
    for name, spec in DISCIPLINES.items():
        if title and spec['title'].search(title):
            title_hits.add(name)
            counts[name] = counts.get(name, 0) + 4
        if joined_body:
            t_matches = len(spec['title'].findall(joined_body))
            b_matches = len(spec['body'].findall(joined_body))
            total = t_matches * 2 + b_matches
            if total >= 2 or (name in title_hits and total >= 1):
                body_hits.add(name)
                counts[name] = counts.get(name, 0) + min(6, total)
    return title_hits, body_hits, counts


def _profile_disciplines(profile: dict, sources: tuple) -> tuple[set[str], dict[str, int]]:
    """Detect the profile's DEMONSTRATED professional disciplines (desired roles are preference only)."""
    exp_titles = _profile_experience_titles(profile)
    role_text = ' | '.join(exp_titles)
    skill_text = ' | '.join(str(s) for s in (profile.get('skills') or []) if s)
    edu_parts: list[str] = []
    for edu in (profile.get('education') or []):
        if isinstance(edu, dict):
            edu_parts.append(
                ' '.join(str(edu.get(k) or '') for k in ('degree', 'field', 'major', 'subject', 'title')).strip()
            )
        elif isinstance(edu, str) and edu.strip():
            edu_parts.append(edu.strip())
    edu_text = ' | '.join(p for p in edu_parts if p)
    content_text = '\n'.join(
        part for part in (*(text for _, text in sources if text), edu_text) if part
    )

    found: set[str] = set()
    strength: dict[str, int] = {}
    for name, spec in DISCIPLINES.items():
        score = 0
        role_hit = bool(role_text and spec['title'].search(role_text))
        if role_hit:
            score += 4
        if skill_text and (spec['title'].search(skill_text) or spec['body'].search(skill_text)):
            score += 2
        if content_text:
            t_cnt = len(spec['title'].findall(content_text))
            b_cnt = len(spec['body'].findall(content_text))
            score += min(6, t_cnt * 2 + b_cnt)
        # Specialist disciplines require either an explicit demonstrated experience role
        # or substantial multi-hit evidence so incidental mentions never qualify as specialist.
        min_threshold = 4 if spec['specialist'] else 2
        if role_hit or score >= min_threshold:
            found.add(name)
            strength[name] = score
    return found, strength


# ---------------------------------------------------------------------------
# Bilingual work-product concepts for domain-aware duty coverage
# ---------------------------------------------------------------------------

_WORK_PRODUCTS: dict[str, tuple[re.Pattern[str], frozenset[str]]] = {
    'software_code': (
        re.compile(r'\b(?:develop\w*\s+(?:software|apis?|services?|applications?|features?)|softwareentwicklung|programmierung|code\s+reviews?|microservices?|backend|frontend|ci/cd)\b', re.I),
        frozenset({'software_engineering', 'devops_cloud'}),
    ),
    'cloud_infra': (
        re.compile(r'\b(?:kubernetes|docker|terraform|cloud\s+infrastructure|deployment\s+pipelines?|server\s+administration|monitoring\s+and\s+alerting|systembetrieb)\b', re.I),
        frozenset({'devops_cloud', 'software_engineering'}),
    ),
    'data_analytics': (
        re.compile(r'\b(?:data\s+(?:analysis|pipelines?|models?|warehousing)|datenanalyse|dashboards?|reporting\s+pipelines?|machine\s+learning\s+models?|statistische\s+auswertung|predictive)\b', re.I),
        frozenset({'data_ml', 'product_management', 'finance_accounting'}),
    ),
    'product_roadmap': (
        re.compile(r'\b(?:product\s+(?:roadmap|backlog|strategy|discovery|requirements?)|produktstrategie|produktentwicklung|user\s+stories|feature\s+prioriti[sz]\w*|priorisierung|anforderungsanalyse)\b', re.I),
        frozenset({'product_management', 'project_operations', 'design_ux'}),
    ),
    'project_delivery': (
        re.compile(r'\b(?:project\s+(?:planning|coordination|delivery|management)|projekt(?:planung|koordination|management|steuerung|umsetzung)|milestones?|meilenstein\w*|timeline|terminplanung)\b', re.I),
        frozenset({'project_operations', 'product_management', 'hr_people_ld', 'administration_office'}),
    ),
    'process_optimization': (
        re.compile(r'\b(?:process\s+(?:improvement|optimi[sz]ation|mapping|documentation)|prozessoptimierung|prozessverbesserung|prozessdokumentation|standardisierung|\bsops?\b|arbeitsabläufe)\b', re.I),
        frozenset({'project_operations', 'product_management', 'logistics_supply_chain', 'administration_office', 'hr_people_ld'}),
    ),
    'learning_training': (
        re.compile(r'\b(?:training\s+(?:programs?|delivery|concepts?|materials?|sessions?)|schulungen|weiterbildung|lernkonzept\w*|e-learning|workshops?\s+(?:design|facilitat|conduct|durchführ|konzipier)|onboarding|einarbeitung|instructional\s+design)\b', re.I),
        frozenset({'hr_people_ld', 'education_social', 'project_operations'}),
    ),
    'hr_recruiting': (
        re.compile(r'\b(?:recruiting|talent\s+acquisition|bewerbungsgespräche|personalbeschaffung|hr\s+business\s+partner|personalbetreuung|mitarbeiterentwicklung|performance\s+reviews?)\b', re.I),
        frozenset({'hr_people_ld'}),
    ),
    'financial_accounting': (
        re.compile(r'\b(?:financial\s+(?:accounting|reporting|closing)|finanzbuchhaltung|jahresabschluss|monatsabschluss|bilanzierung|kreditoren|debitoren|lohnabrechnung|controlling|budgetierung|kostenrechnung)\b', re.I),
        frozenset({'finance_accounting'}),
    ),
    'legal_contracts': (
        re.compile(r'\b(?:legal\s+(?:advice|counsel|review|opinions?)|rechtsberatung|vertragsprüfung|vertragsgestaltung|contract\s+negotiation|compliance\s+(?:program|audit)|datenschutz|gdpr|dsgvo)\b', re.I),
        frozenset({'legal_compliance'}),
    ),
    'sales_pipeline': (
        re.compile(r'\b(?:sales\s+pipeline|new\s+business|neukundenakquise|cold\s+calling|kaltakquise|key\s+account|kundenakquise|angebotserstellung|vertragsabschluss|upselling)\b', re.I),
        frozenset({'sales_bd', 'customer_support_success'}),
    ),
    'marketing_campaigns': (
        re.compile(r'\b(?:marketing\s+(?:campaigns?|strategy)|marketingkampagnen|performance\s+marketing|seo|content\s+(?:creation|marketing)|social\s+media|brand\s+strategy|pressemitteilungen)\b', re.I),
        frozenset({'marketing_growth', 'design_ux'}),
    ),
    'ux_prototyping': (
        re.compile(r'\b(?:user\s+(?:research|interviews?|testing|flows?|journeys?)|nutzerforschung|usability|wireframes?|mockups?|prototyp\w*|figma|design\s+systems?)\b', re.I),
        frozenset({'design_ux', 'product_management', 'mechanical_hardware'}),
    ),
    'customer_support': (
        re.compile(r'\b(?:customer\s+(?:support|service|inquiries|onboarding|retention)|kundenservice|kundenbetreuung|kundenanfragen|support\s+tickets?|beschwerdemanagement)\b', re.I),
        frozenset({'customer_support_success', 'sales_bd', 'administration_office'}),
    ),
    'clinical_nursing': (
        re.compile(r'\b(?:patient\s+care|patientenversorgung|krankenpflege|altenpflege|pflegeplanung|medikamentengabe|therapie|diagnose|visite)\b', re.I),
        frozenset({'healthcare_clinical'}),
    ),
    'lab_pharma': (
        re.compile(r'\b(?:gmp|clinical\s+trials?|klinische\s+studien|pharmacovigilance|laboranalytik|hplc|pcr|cell\s+culture|qualifizierung\s+und\s+validierung)\b', re.I),
        frozenset({'pharma_biotech'}),
    ),
    'cad_mechanical': (
        re.compile(r'\b(?:technical\s+drawings?|technische\s+zeichnungen|cad|solidworks|inventor|catia|creo|bill\s+of\s+materials|stückliste|prototyp(?:e|es|ing)|funktionsmuster\w*|prototypenbau|fixtures?|test\s+rigs?|versuchsaufbau\w*|versuchsdokumentation|3d[- ]?druck|3d\s+printing|maschinenbau|konstruktion|fertigung|manufacturing)\b', re.I),
        frozenset({'mechanical_hardware', 'electrical_embedded'}),
    ),
    'electrical_controls': (
        re.compile(r'\b(?:schaltpläne|circuit\s+design|pcb|sps|plc|tia\s+portal|automatisierungstechnik|steuerungstechnik|elektrotechnik)\b', re.I),
        frozenset({'electrical_embedded', 'mechanical_hardware'}),
    ),
    'civil_site': (
        re.compile(r'\b(?:bauleitung|bauüberwachung|baustellen|hoai|vob|bim|revit|tragwerksplanung|genehmigungsplanung)\b', re.I),
        frozenset({'civil_construction'}),
    ),
    'supply_logistics': (
        re.compile(r'\b(?:supply\s+chain|lieferkette|procurement|einkauf|disposition|dispatching|wareneingang|warenausgang|inbound|outbound|kommissionierung|picking|packing|fulfillment|lagerverwaltung|warehouse\s+(?:management|operations)|warehousing|\bwms\b|freight|shipping|shipments?|carriers?|route\s+planning|tourenplanung|fleet|fuhrpark|inventory|bestandsmanagement|zollabwicklung|customs|lieferantenkoordination|supplier\s+coordination)\b', re.I),
        frozenset({'logistics_supply_chain', 'project_operations'}),
    ),
    'executive_admin': (
        re.compile(r'\b(?:executive\s+support|administrative\s+support|büroorganisation|terminkoordination|reisekosten|korrespondenz|protokollführung|calendar\s+management)\b', re.I),
        frozenset({'administration_office', 'project_operations'}),
    ),
}


# ---------------------------------------------------------------------------
# Formal prerequisite detection (unverified regulated licenses / degrees)
# ---------------------------------------------------------------------------

_FORMAL_LICENSE_RE = re.compile(
    r'\b(?:'
    r'approbation|facharzt\w*|pflegeexamen|examiniert\w*\s+(?:pflege|kranken|alten)|'
    r'staatsexamen|volljurist\w*|befähigung\s+zum\s+richteramt|zulassung\s+als\s+rechtsanwalt|'
    r'steuerberaterexamen|wirtschaftsprüferexamen|'
    r'meisterbrief|meistertitel|handwerksmeister|industriemeister|'
    r'staatliche\s+anerkennung\s+als\s+(?:erzieher|sozial|heilerziehungs)|'
    r'cpa\b|chartered\s+accountant|bar\s+admission|licensed\s+(?:attorney|nurse|physician|architect|engineer)|'
    r'professional\s+engineer\s*\(pe\)|security\s+clearance|sicherheitsüberprüfung\s*(?:ü[123])?'
    r')\b',
    re.I,
)


# ---------------------------------------------------------------------------
# Content token overlap (stopword-filtered, language/visa-free continuous signal)
# ---------------------------------------------------------------------------

_CONTENT_STOPWORDS: frozenset[str] = frozenset({
    # English function + generic job-ad words
    'about', 'above', 'across', 'after', 'again', 'against', 'also', 'always', 'among', 'another',
    'apply', 'around', 'available', 'based', 'because', 'become', 'been', 'before', 'being',
    'benefits', 'between', 'both', 'bring', 'business', 'candidate', 'candidates', 'career',
    'company', 'culture', 'daily', 'different', 'diverse', 'during', 'each', 'either', 'employee',
    'employees', 'employer', 'english', 'ensure', 'environment', 'equal', 'every', 'excellent',
    'experience', 'experienced', 'first', 'flexible', 'fluent', 'from', 'full', 'german', 'germany',
    'good', 'great', 'group', 'growth', 'have', 'help', 'here', 'high', 'highly', 'hiring', 'hybrid',
    'ideal', 'ideally', 'impact', 'including', 'into', 'join', 'just', 'knowledge', 'language',
    'languages', 'large', 'leading', 'learn', 'level', 'like', 'location', 'looking', 'make',
    'making', 'many', 'more', 'most', 'must', 'native', 'need', 'needs', 'office', 'offer',
    'online', 'only', 'open', 'opportunity', 'other', 'others', 'over', 'part', 'passion', 'people',
    'permit', 'place', 'plus', 'position', 'positions', 'preferred', 'proficiency', 'profile',
    'provide', 'qualifications', 'related', 'relevant', 'remote', 'required', 'requirements',
    'responsibilities', 'right', 'role', 'roles', 'same', 'seeking', 'should', 'skills', 'some',
    'speak', 'speaking', 'spoken', 'sponsorship', 'strong', 'such', 'support', 'team', 'teams',
    'than', 'that', 'their', 'them', 'then', 'there', 'these', 'they', 'this', 'through', 'time',
    'together', 'under', 'understanding', 'very', 'visa', 'want', 'well', 'were', 'what', 'when',
    'where', 'which', 'while', 'will', 'with', 'within', 'without', 'work', 'working', 'world',
    'would', 'written', 'year', 'years', 'your',
    # German function + generic job-ad words
    'aber', 'alle', 'allem', 'allen', 'aller', 'alles', 'also', 'anforderungen', 'angebot',
    'arbeit', 'arbeiten', 'arbeitgeber', 'arbeitsplatz', 'arbeitsweise', 'auch', 'aufgaben',
    'ausgezeichnete', 'bereich', 'berlin', 'berufserfahrung', 'beste', 'bewerbung', 'bieten',
    'bringst', 'bringen', 'dabei', 'dadurch', 'dafür', 'daher', 'damit', 'dann', 'darüber',
    'dass', 'dein', 'deine', 'deinem', 'deinen', 'deiner', 'deines', 'dem', 'denen', 'deren',
    'deshalb', 'deutsch', 'deutsche', 'deutschen', 'deutschkenntnisse', 'deutschland', 'dich',
    'diese', 'diesem', 'diesen', 'dieser', 'dieses', 'doch', 'durch', 'eigenständig', 'eine',
    'einem', 'einen', 'einer', 'eines', 'einsatz', 'englisch', 'englischkenntnisse', 'erfahrung',
    'erfahrungen', 'erforderlich', 'erwartet', 'etwas', 'fachlich', 'firma', 'flexibel', 'flexible',
    'fließend', 'fließende', 'freude', 'fundierte', 'ganz', 'gemeinsam', 'gerne', 'gute', 'guten',
    'guter', 'haben', 'hast', 'heute', 'hier', 'hohe', 'hohes', 'homeoffice', 'ihre', 'ihrem',
    'ihren', 'ihrer', 'immer', 'interessante', 'jahr', 'jahre', 'jahren', 'jede', 'jedem', 'jeden',
    'jeder', 'jedoch', 'jetzt', 'kann', 'kannst', 'kein', 'keine', 'kenntnisse', 'kleine', 'kollegen',
    'können', 'kultur', 'leben', 'mehr', 'mindestens', 'mitarbeiter', 'mitarbeitende', 'modern',
    'moderne', 'möglich', 'münchen', 'nach', 'neue', 'neuen', 'neuer', 'neues', 'nicht', 'noch',
    'oder', 'ohne', 'persönlich', 'position', 'profil', 'projekt', 'projekte', 'projekten',
    'rahmen', 'rolle', 'rund', 'schrift', 'sehr', 'sein', 'seine', 'seinem', 'seinen', 'seiner',
    'seit', 'selbstständig', 'sich', 'sicher', 'sichere', 'sind', 'sollten', 'sowie', 'spannende',
    'sprache', 'sprachen', 'sprachkenntnisse', 'stark', 'starke', 'stelle', 'stellen', 'suchen',
    'tätig', 'tätigkeit', 'teil', 'teilzeit', 'über', 'umfeld', 'unser', 'unsere', 'unserem',
    'unseren', 'unserer', 'unter', 'unternehmen', 'unterstützung', 'verantwortung', 'verfügst',
    'verfügen', 'verhandlungssicher', 'verschiedene', 'viele', 'vollzeit', 'voraussetzung',
    'voraussetzungen', 'vorteil', 'während', 'weiter', 'weitere', 'weiterentwicklung', 'welt',
    'wenn', 'werden', 'wichtig', 'wird', 'wirst', 'wort', 'wünschenswert', 'zeit', 'zusammen',
    'zusammenarbeit', 'zwingend',
})
_CONTENT_TOK_RE = re.compile(r'[a-zäöüß]{4,}')


def _extract_content_tokens(text: str) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(
        tok
        for tok in _CONTENT_TOK_RE.findall(text.casefold())
        if tok not in _CONTENT_STOPWORDS
    )


# ---------------------------------------------------------------------------
# Role alignment helper
# ---------------------------------------------------------------------------

_ROLE_STOP = frozenset({
    'm', 'w', 'd', 'f', 'x', 'gn', 'all', 'genders', 'and', 'und', 'for', 'für',
    'in', 'at', 'bei', 'of', 'der', 'die', 'das', 'the', 'a', 'an', 'with', 'mit',
    'to', 'on', 'or', 'oder', 'senior', 'junior', 'lead', 'head', 'intern',
    'working', 'student', 'werkstudent', 'full', 'time', 'part', 'remote', 'hybrid',
    'berlin', 'munich', 'münchen', 'hamburg', 'frankfurt', 'germany', 'deutschland',
    'zurich', 'zürich', 'vienna', 'wien', 'london', 'amsterdam', 'europe',
})


def _role_tokens(text: str) -> frozenset[str]:
    clean = re.sub(r'\((?:[mwdfhx]\s*[/|&]\s*){1,3}[mwdfhx]\)|\((?:all genders|gn|divers)\)', ' ', text, flags=re.I)
    return frozenset(
        tok
        for tok in re.findall(r'[a-zäöüß0-9+/#&-]{2,}', clean.casefold())
        if tok not in _ROLE_STOP
    )


@lru_cache(maxsize=2048)
def _cached_pattern(term: str) -> re.Pattern[str]:
    return _pattern(term)


_ROLE_GROUP_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(
        r'(?<![\w+#])(?:' + '|'.join(re.escape(term) for term in group) + r')(?![\w+#])',
        re.I,
    )
    for group in ALL_ROLE_GROUPS
)


def _role_group_ids(text: str) -> frozenset[int]:
    if not text:
        return frozenset()
    return frozenset(
        idx
        for idx, pat in enumerate(_ROLE_GROUP_PATTERNS)
        if pat.search(text)
    )


# ---------------------------------------------------------------------------
# Cached profile-side extraction (reused across all jobs for the same profile)
# ---------------------------------------------------------------------------

_PROFILE_CACHE_FIELDS = (
    'skills',
    'skill_evidence',
    'desired_roles',
    'secondary_roles',
    'experience_entries',
    'experience_years',
    'seniority',
    'education',
    'cv_text',
    'summary',
    'headline',
    'duty_evidence',
    'cv_career_facts',
    'content_sources',
    'application_personalization',
    'application_details',
    'work_styles',
    'employment_types',
)


def _extract_profile_task_facts(
    profile_sources: tuple[tuple[str, str], ...],
    source_contexts: dict[str, str],
) -> tuple[tuple[str, str, frozenset[str], frozenset[str], str], ...]:
    """Cached profile-side fact extraction identical to task_alignment.align_tasks."""
    facts: list[tuple[str, str, frozenset[str], frozenset[str], str]] = []
    for source, text in islice(profile_sources, 80):
        if not isinstance(text, str):
            continue
        for full_sentence, _ in _ta._sentences(text[:12000]):
            full_sentence = full_sentence[:1000]
            bounded = bool(_ta._BOUNDED.search(full_sentence))
            for sentence, _ in _ta._atomic_duties(full_sentence):
                if _ta._excluded(sentence) or _ta._INTEREST.search(sentence):
                    continue
                actions = _ta._profile_actions(sentence)
                objects = _ta._semantic_objects(sentence)
                if actions and objects:
                    quote = full_sentence if bounded else sentence
                    declared = source_contexts.get(source)
                    context = (
                        'academic' if _ta._academic(source, quote) else
                        declared if declared in {'academic', 'research', 'professional', 'unspecified'} else
                        'professional' if source.startswith(('experience.', 'cv_career_facts.experiences[')) else
                        'unspecified'
                    )
                    facts.append((source, quote, frozenset(actions), frozenset(objects), context))
                if len(facts) >= 240:
                    break
            if len(facts) >= 240:
                break
        if len(facts) >= 240:
            break
    return tuple(facts)


def _align_tasks_with_facts(
    tasks: tuple,
    facts: tuple[tuple[str, str, frozenset[str], frozenset[str], str], ...],
) -> dict[str, Any]:
    """Run task_alignment matching against pre-extracted profile task facts."""
    rows = []
    for task, objects in tasks:
        best = None
        best_rank = (0.0, 0.0)
        actions = set(task['actions'])
        for source, quote, fact_actions, fact_objects, context in facts:
            shared = objects & fact_objects
            if not shared:
                continue
            coverage = len(shared) / max(1, len(objects))
            common_action = bool(actions & fact_actions)
            ownership_missing = 'own' in actions and 'own' not in fact_actions
            bounded_scope = (
                context in {'academic', 'research'}
                or bool(_ta._BOUNDED.search(quote))
                or 'support' in fact_actions
            )
            ownership_missing = ownership_missing or (
                bounded_scope and ('own' in actions or task['independent_scope'])
            )
            direct = (
                common_action
                and len(shared) >= min(2, len(objects))
                and coverage >= 0.5
                and not ownership_missing
                and not ((objects - shared) and (fact_objects - shared))
            )
            adjacent_action = any(
                frozenset((action, fact_action)) in _ta._ADJACENT_ACTIONS
                for action in actions
                for fact_action in fact_actions
            )
            adjacent = bool(shared) and (
                (common_action and objects == fact_objects)
                or len(shared) >= 2
                or (
                    any(obj.startswith('concept:') for obj in shared)
                    and (common_action or adjacent_action)
                )
            )
            support_lifecycle = (
                'support' in actions
                and bool(fact_actions & {'create', 'prepare', 'maintain'})
                and any(obj.startswith('concept:') for obj in shared)
                and objects <= fact_objects
            )
            adjacent = adjacent or support_lifecycle
            strength = 1.0 if direct else 0.5 if adjacent else 0.0
            if (strength, coverage) > best_rank:
                best_rank = (strength, coverage)
                best = (source, quote, sorted(set(task['objects']) & _ta._objects(quote)), context)
        status = 'supported' if best_rank[0] == 1.0 else 'transferable' if best_rank[0] else 'unknown'
        source, quote, shared_objs, context = best or ('', '', [], 'unspecified')
        rows.append({
            **task,
            'actions': list(task['actions']),
            'objects': list(task['objects']),
            'status': status,
            'evidence_source': source,
            'evidence_quote': quote,
            'evidence_context': context,
            'shared_objects': shared_objs,
            'explanation': {
                'supported': 'Same documented action and equivalent deliverable; verify any separate authority, domain or tenure prerequisites.',
                'transferable': 'Adjacent action or deliverable evidence; complete task scope is not established.',
                'unknown': 'No source-backed action and deliverable alignment found; absence is not proof of inability.',
            }[status],
        })
    coverage = (
        sum({'supported': 1.0, 'transferable': 0.5, 'unknown': 0.0}[row['status']] for row in rows)
        / len(rows) if rows else 0.0
    )
    return {
        'version': _ta.VERSION,
        'score': round(100 * coverage) if rows else None,
        'coverage': coverage,
        'requirements': rows,
        'facts_considered': len(facts),
        'qualification_proof': False,
    }


def _profile_cache_key(profile: dict) -> str:
    projection = {k: profile.get(k) for k in _PROFILE_CACHE_FIELDS if k in profile}
    return json.dumps(projection, sort_keys=True, ensure_ascii=False, default=str)


@lru_cache(maxsize=128)
def _precompute_profile_cached(key_json: str) -> dict[str, Any]:
    profile = json.loads(key_json)

    raw_sources = list(profile_content_sources(profile))
    if isinstance(profile.get('cv_text'), str) and profile['cv_text'].strip():
        raw_sources.append(('cv_text', profile['cv_text'].strip()))
    sources = tuple(dict.fromkeys((src, txt) for src, txt in raw_sources if txt))
    source_contexts = dict(_profile_skill_contexts(profile))

    duty_references: dict[str, Any] = {}
    extra_duty_sources: list[tuple[str, str, str, str]] = []
    for idx, row in enumerate((profile.get('duty_evidence') or [])[:20]):
        if isinstance(row, dict) and isinstance(row.get('quote'), str):
            src_key = f'duty_evidence[{idx}]'
            extra_duty_sources.append((
                src_key,
                row['quote'],
                str(row.get('context', '')),
                str(row.get('responsibility', '')),
            ))
            if row.get('reference') is not None:
                duty_references[src_key] = row.get('reference')

    duty_sources = tuple(
        (src, val, source_contexts.get(src, ''), '')
        for src, val in sources
    ) + tuple(extra_duty_sources)

    exp_titles = tuple(_profile_experience_titles(profile))
    desired_roles = tuple(str(r).strip() for r in (profile.get('desired_roles') or []) if str(r).strip())
    secondary_roles = tuple(str(r).strip() for r in (profile.get('secondary_roles') or []) if str(r).strip())

    # Demonstrated profile text ONLY — desired_roles and secondary_roles are preference only!
    edu_texts: list[str] = []
    for edu in (profile.get('education') or []):
        if isinstance(edu, dict):
            edu_texts.append(' '.join(str(v) for v in edu.values() if v))
        elif isinstance(edu, str) and edu.strip():
            edu_texts.append(edu.strip())
    profile_full_text = '\n'.join(
        part
        for part in (
            ' '.join(exp_titles),
            ' '.join(str(s) for s in (profile.get('skills') or []) if s),
            ' '.join(edu_texts),
            *(txt for _, txt in sources if txt),
            *(val for _, val, _, _ in duty_sources if val),
        )
        if part and part.strip()
    )
    has_profile_evidence = bool(
        profile_full_text.strip()
        or profile.get('experience_years') is not None
        or str(profile.get('seniority') or '').strip()
    )

    prof_disc, prof_disc_strength = _profile_disciplines(profile, sources)
    prof_products = frozenset(
        wp for wp, (pat, _) in _WORK_PRODUCTS.items()
        if profile_full_text and pat.search(profile_full_text)
    )

    profile_skills_norm = frozenset(normalize_skills(profile.get('skills') or []))
    profile_evidenced_skills = set(profile_skills_norm)
    if profile_full_text:
        for skill, comb_pat in _SKILL_COMBINED.items():
            if skill not in profile_evidenced_skills and comb_pat.search(profile_full_text):
                profile_evidenced_skills.add(skill)

    custom_profile_skills = tuple(
        str(s).strip()
        for s in (profile.get('skills') or [])
        if str(s).strip() and str(s).strip() not in ALL_SKILLS
    )

    prof_sen, prof_years, prof_sen_quote = _profile_seniority_and_years(profile)
    task_facts = _extract_profile_task_facts(sources, source_contexts)
    content_tokens = _extract_content_tokens(profile_full_text)

    # Precompute role patterns, group IDs, and tokens for fast role_alignment.
    def _prep_roles(roles: tuple[str, ...]) -> tuple[tuple[str, re.Pattern[str], frozenset[int], frozenset[str]], ...]:
        return tuple((r, _cached_pattern(r), _role_group_ids(r), _role_tokens(r)) for r in roles)

    raw_edu = profile.get('education')
    if isinstance(raw_edu, list) and all(isinstance(e, dict) for e in raw_edu):
        edu_profile = profile
    elif isinstance(raw_edu, list):
        edu_profile = {**profile, 'education': [e for e in raw_edu if isinstance(e, dict)]}
    else:
        edu_profile = {**profile, 'education': []}

    return {
        'sources': sources,
        'source_contexts': source_contexts,
        'skill_contexts': {skill: _skill_context(profile, skill) for skill in profile_evidenced_skills},
        'duty_sources': duty_sources,
        'duty_references': duty_references,
        'task_facts': task_facts,
        'exp_titles': exp_titles,
        'desired_prep': _prep_roles(desired_roles),
        'secondary_prep': _prep_roles(secondary_roles),
        'exp_prep': _prep_roles(exp_titles),
        'profile_full_text': profile_full_text,
        'has_profile_evidence': has_profile_evidence,
        'prof_disc': frozenset(prof_disc),
        'prof_disc_strength': prof_disc_strength,
        'prof_products': prof_products,
        'profile_skills_norm': profile_skills_norm,
        'profile_evidenced_skills': frozenset(profile_evidenced_skills),
        'custom_profile_skills': custom_profile_skills,
        'prof_sen': prof_sen,
        'prof_years': prof_years,
        'prof_sen_quote': prof_sen_quote,
        'content_tokens': content_tokens,
        'edu_profile': edu_profile,
        'pref_styles': frozenset(
            str(s).strip().casefold()
            for s in (profile.get('work_styles') or [])
            if str(s).strip()
        ),
        'pref_emp': frozenset(
            str(e).strip().casefold().replace('_', '-').replace(' ', '-')
            for e in (profile.get('employment_types') or [])
            if str(e).strip()
        ),
    }


def precompute_profile(profile: dict) -> dict[str, Any]:
    """Return cached profile-side extractions for fast repeated scoring across jobs."""
    return _precompute_profile_cached(_profile_cache_key(profile))


def _compute_role_alignment(
    job_title: str,
    profile_cache: dict[str, Any],
    job_title_disciplines: set[str],
    prof_disciplines: frozenset[str],
) -> tuple[float, str, str, str]:
    """Compute role_alignment in [0, 1] + (detail, profile_source, profile_quote)."""
    desired_prep = profile_cache['desired_prep']
    secondary_prep = profile_cache['secondary_prep']
    exp_prep = profile_cache['exp_prep']
    if not desired_prep and not secondary_prep and not exp_prep:
        return 0.35, 'No saved target roles; neutral role alignment', '', ''

    title_groups = _role_group_ids(job_title)
    title_toks = _role_tokens(job_title)
    title_pat = _cached_pattern(job_title) if len(job_title) >= 4 else None

    best_score = 0.0
    best_src = ''
    best_quote = ''
    best_detail = 'Job title did not align with saved target roles'

    def _eval_role_list(
        roles_prep: tuple[tuple[str, re.Pattern[str], frozenset[int], frozenset[str]], ...],
        source_name: str,
        exact_base: float,
        exact_span: float,
        group_base: float,
        group_span: float,
        tok_w: float,
    ) -> None:
        nonlocal best_score, best_src, best_quote, best_detail
        for role, r_pat, r_groups, r_toks in roles_prep:
            union_len = len(title_toks | r_toks)
            tok_jacc = (len(title_toks & r_toks) / union_len) if union_len else 0.0
            if r_pat.search(job_title) or (title_pat is not None and title_pat.search(role)):
                sc = exact_base + exact_span * tok_jacc
                if sc > best_score:
                    best_score = sc
                    best_src = source_name
                    best_quote = role
                    best_detail = f'Exact title match with {source_name}: {role}'
                continue
            if title_groups and r_groups and (title_groups & r_groups):
                sc = group_base + group_span * tok_jacc
                if sc > best_score:
                    best_score = sc
                    best_src = source_name
                    best_quote = role
                    best_detail = f'Role-family match with {source_name}: {role}'
                continue
            if title_toks and r_toks:
                overlap = title_toks & r_toks
                if overlap:
                    min_cov = len(overlap) / max(1, min(len(title_toks), len(r_toks)))
                    sc = tok_w * (0.65 * min(1.0, min_cov) + 0.35 * tok_jacc)
                    if sc > best_score:
                        best_score = sc
                        best_src = source_name
                        best_quote = role
                        best_detail = f'Partial title token overlap ({", ".join(sorted(overlap))}) with {role}'

    _eval_role_list(desired_prep, 'profile.desired_roles', 0.90, 0.10, 0.78, 0.10, 0.55)
    _eval_role_list(secondary_prep, 'profile.secondary_roles', 0.74, 0.08, 0.64, 0.08, 0.45)
    _eval_role_list(exp_prep, 'profile.experience_entries', 0.65, 0.08, 0.55, 0.08, 0.38)

    # Modest cross-language title-discipline fallback when job title discipline directly matches
    # a demonstrated profile discipline (never for loose adjacency or desired-only roles).
    if job_title_disciplines and prof_disciplines:
        shared = job_title_disciplines & prof_disciplines
        if shared and best_score < 0.35:
            best_score = 0.35
            best_detail = f'Title discipline matches profile ({", ".join(sorted(shared))})'
            best_src = best_src or 'profile.disciplines'
            best_quote = best_quote or ', '.join(sorted(shared))

    return round(min(1.0, max(0.0, best_score)), 4), best_detail, best_src, best_quote


# ---------------------------------------------------------------------------
# Main feature extraction entry point
# ---------------------------------------------------------------------------

# Identifiers in otherwise identical duty lines do not add another work product.
_DUTY_REFERENCE_SUFFIX = re.compile(r'(?<![\w.+-])\d+([.!?]?)$')


@lru_cache(maxsize=256)
def _analyze_job(title: str, parsed_texts: tuple) -> dict[str, Any]:
    """Analyze bounded scoring evidence once per hashable posting content and title."""
    # Strip ONLY language/visa sub-clauses so mixed sentences (e.g. "You must speak fluent
    # German and have advanced Kubernetes experience") preserve sibling requirement evidence
    # while ablating language/visa clauses has ZERO leakage into content features.
    filtered_sentences: list[tuple[str, str, str]] = []  # (source, cleaned_sentence, scope)
    seen: set[tuple[str, str]] = set()
    remaining = 24_000
    for source, force_req, sentences in parsed_texts:
        # Skip dedicated language/authorization metadata blocks from content features.
        if force_req:
            continue
        for sentence, scope in sentences:
            if remaining <= 0 or len(filtered_sentences) >= 160:
                break
            if is_source_instruction(sentence):
                continue
            cleaned = strip_language_or_visa_subclauses(sentence)
            if not cleaned:
                continue
            normalized = ' '.join(cleaned.casefold().split())
            if scope == 'duties':
                normalized = _DUTY_REFERENCE_SUFFIX.sub(r'\1', normalized).rstrip(' .!?')
            key = (normalized, scope)
            if key in seen:
                continue
            seen.add(key)
            cleaned = cleaned[:min(remaining, 1500)]
            remaining -= len(cleaned)
            filtered_sentences.append((source, cleaned, scope))

    filtered_body_texts = [sent for _, sent, scope in filtered_sentences if scope != 'benefits']
    filtered_body = '\n'.join(filtered_body_texts)
    duty_sentences = [
        (src, sent, scope) for src, sent, scope in filtered_sentences
        if src == 'description' or scope == 'duties'
    ]
    job = {'title': title}
    task_units = _ta.task_units(job, sentences=duty_sentences, deduplicate_deliverables=True)
    duty_analysis = analyze_duty_units(
        job, task_units, [(sent, scope) for src, sent, scope in duty_sentences if src == 'description'],
    )
    tasks = tuple((task, _ta._semantic_objects(task['job_quote'])) for task in task_units)

    combined_text = f'{title}\n{filtered_body}'
    normalized_text = combined_text.translate(_SKILL_CASE_TRANSLATION).lower()
    candidate_skills = tuple(
        skill for skill, literals in _SKILL_LITERALS.items()
        if any(literal in normalized_text for literal in literals) and _SKILL_COMBINED[skill].search(combined_text)
    )
    mentions = [
        (skill, 'title', title[:180], 'required', 1.3)
        for skill in candidate_skills if _SKILL_COMBINED[skill].search(title)
    ]
    for src, sentence, scope in filtered_sentences:
        for skill in candidate_skills:
            if match := _SKILL_COMBINED[skill].search(sentence):
                kind = classify_skill_mention(sentence, match.start(), match.end(), scope)
                if kind != 'incidental':
                    weight = 1.0 if kind == 'required' else 0.75 if kind == 'duty' else 0.35
                    mentions.append((skill, src, sentence[:200], kind, weight))

    job_product_quotes: dict[str, str] = {}
    if filtered_body:
        for wp, (pat, _) in _WORK_PRODUCTS.items():
            m_job = pat.search(filtered_body)
            if m_job:
                s0 = max(0, m_job.start() - 35)
                s1 = min(len(filtered_body), m_job.end() + 55)
                job_product_quotes[wp] = ' '.join(filtered_body[s0:s1].split())

    qual_rows: list[dict[str, Any]] = []
    for source, sentence, scope in filtered_sentences:
        if scope in {'optional', 'benefits'}:
            continue
        if scope == 'requirements' or REQUIREMENT.search(sentence):
            qual_rows.extend(qualification_rows(sentence, source))

    return {
        'sentences': filtered_sentences,
        'body': filtered_body,
        'content_tokens': _extract_content_tokens(filtered_body),
        'disciplines': _detect_disciplines(title, filtered_body_texts),
        'specialist_duties': tuple(d for d, pattern in _SPECIALIST_DUTY_PATTERNS if pattern.search(filtered_body)),
        'task_units': tasks,
        'duties': duty_analysis,
        'combined_text': combined_text,
        'candidate_skills': candidate_skills,
        'skill_mentions': mentions,
        'product_quotes': job_product_quotes,
        'qualification_rows': qual_rows,
        'tenure_minimums': _tenure_minimums(filtered_sentences),
        'substantive_word_count': len(re.findall(r'[A-Za-zÄÖÜäöüß]{3,}', filtered_body)),
    }


def extract_features(
    job: dict,
    profile: dict,
    enriched: dict,
    gate_result: dict,
    *,
    semantic: dict | None = None,
    profile_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute all interpretable [0,1] features, bounded soft penalties, and citations."""
    pc = profile_cache if profile_cache is not None else precompute_profile(profile)

    title = plain_text(job.get('title'))
    parsed_texts = gate_result.get('parsed_texts')
    if parsed_texts is None:
        parsed_texts = parse_job_requirement_texts(job_requirement_texts(job))
    analysis = _analyze_job(title, parsed_texts)
    filtered_sentences = analysis['sentences']
    filtered_body = analysis['body']

    sources = pc['sources']
    duty_sources = pc['duty_sources']
    duty_references = pc['duty_references']
    profile_full_text = pc['profile_full_text']
    has_profile_evidence = pc['has_profile_evidence']
    prof_disc: frozenset[str] = pc['prof_disc']
    prof_disc_strength: dict[str, int] = pc['prof_disc_strength']
    prof_products: frozenset[str] = pc['prof_products']
    profile_skills_norm: frozenset[str] = pc['profile_skills_norm']
    profile_evidenced_skills = set(pc['profile_evidenced_skills'])
    custom_profile_skills: tuple[str, ...] = pc['custom_profile_skills']
    prof_content_tokens: frozenset[str] = pc['content_tokens']

    # Continuous stopword-filtered content-token overlap (language/visa-free).
    job_content_tokens = analysis['content_tokens']
    if has_profile_evidence and job_content_tokens and prof_content_tokens:
        tok_hits = len(job_content_tokens & prof_content_tokens)
        raw_lex = tok_hits / max(1.0, min(len(job_content_tokens), 45) + 8.0)
        content_overlap = min(1.0, 1.0 - math.exp(-3.2 * raw_lex))
    else:
        content_overlap = 0.0

    # -----------------------------------------------------------------------
    # 1. Disciplines: domain_alignment & domain_mismatch (smoothly continuous)
    # -----------------------------------------------------------------------
    job_title_disc, job_body_disc, job_disc_counts = analysis['disciplines']
    job_disc = job_title_disc | job_body_disc

    if has_profile_evidence and job_disc and prof_disc:
        weighted_total = 0.0
        weighted_aligned = 0.0
        for d in job_disc:
            depth_w = 1.0 - 0.35 / math.sqrt(job_disc_counts.get(d, 1) + 1)
            w = (2.5 if d in job_title_disc else 1.0) * depth_w
            weighted_total += w
            if d in prof_disc:
                prof_str = 0.78 + 0.22 * (min(10, prof_disc_strength.get(d, 4)) / 10.0)
                weighted_aligned += w * prof_str
            elif ADJACENT_DISCIPLINES.get(d, set()) & prof_disc:
                weighted_aligned += 0.42 * w
        domain_alignment = weighted_aligned / max(1e-6, weighted_total)
        if weighted_aligned == 0.0:
            domain_alignment = 0.06 * content_overlap
    elif not has_profile_evidence:
        domain_alignment = 0.0
    elif not job_disc:
        domain_alignment = 0.16 + 0.12 * content_overlap
    else:
        domain_alignment = 0.06 + 0.06 * content_overlap

    # Specialist domain mismatch: smooth depth-based curve to avoid discrete pile-up at 1.0.
    uncovered_specialist: list[str] = []
    specialist_mismatch_num = 0.0
    specialist_mismatch_den = 0.0
    for d in job_disc:
        if not DISCIPLINES[d]['specialist']:
            continue
        w = 2.5 if d in job_title_disc else 1.0
        specialist_mismatch_den += w
        if d in prof_disc:
            continue
        if ADJACENT_DISCIPLINES.get(d, set()) & prof_disc:
            specialist_mismatch_num += 0.45 * w
        else:
            specialist_mismatch_num += w
            uncovered_specialist.append(d)

    specialist_duty_hits = 0
    for d in analysis['specialist_duties']:
        if d not in prof_disc:
            specialist_mismatch_den += 1.5
            specialist_duty_hits += 1
            if ADJACENT_DISCIPLINES.get(d, set()) & prof_disc:
                specialist_mismatch_num += 0.6
            else:
                specialist_mismatch_num += 1.5
                if d not in uncovered_specialist:
                    uncovered_specialist.append(d)

    if specialist_mismatch_den > 0 and specialist_mismatch_num > 0:
        depth_bonus = 0.14 * sum(min(6, job_disc_counts.get(d, 1)) for d in uncovered_specialist)
        raw_mismatch = specialist_mismatch_num + depth_bonus + 0.20 * specialist_duty_hits
        ratio_floor = specialist_mismatch_num / max(2.5, specialist_mismatch_den)
        smooth_depth = raw_mismatch / (raw_mismatch + 1.45)
        domain_mismatch = min(0.98, 0.45 * ratio_floor + 0.55 * smooth_depth)
    elif specialist_mismatch_den > 0:
        domain_mismatch = 0.0
    else:
        if job_title_disc and prof_disc and not (job_title_disc & prof_disc):
            has_adj = any(ADJACENT_DISCIPLINES.get(d, set()) & prof_disc for d in job_title_disc)
            domain_mismatch = (0.18 if has_adj else 0.62) * (1.0 - 0.15 * content_overlap)
        elif job_body_disc and prof_disc and not (job_disc & prof_disc):
            has_adj = any(ADJACENT_DISCIPLINES.get(d, set()) & prof_disc for d in job_body_disc)
            domain_mismatch = (0.14 if has_adj else 0.48) * (1.0 - 0.15 * content_overlap)
        else:
            domain_mismatch = 0.0

    domain_agreement_factor = max(0.15, min(1.0, 1.0 - 0.80 * domain_mismatch))

    # -----------------------------------------------------------------------
    # 2. Duty coverage (domain-aware: assess_duties + cached align_tasks + work products)
    # -----------------------------------------------------------------------
    task_alignment = _align_tasks_with_facts(analysis['task_units'], pc['task_facts'])
    duty_evidence = assess_duties(job, duty_sources, task_alignment, analysis=analysis['duties'])

    # Re-evaluate assess_duties rows with domain awareness (reading real 'credit' field).
    duty_rows = duty_evidence.get('duties') or []
    adjusted_duty_scores: list[float] = []
    best_duty_quote = ''
    best_duty_psrc = ''
    best_duty_pquote = ''
    best_duty_s = -1.0
    for row in duty_rows:
        # Attach profile duty reference if available (v1 parity).
        ev_src = str(row.get('evidence_source') or '')
        if ev_src in duty_references and 'evidence_reference' not in row:
            row['evidence_reference'] = duty_references[ev_src]
        jq = str(row.get('job_quote') or '')
        if is_language_or_visa_clause(jq):
            continue
        raw_s = float(
            row.get('credit')
            if row.get('credit') is not None
            else (row.get('score') or 0.0)
        )
        row_disc = {
            d for d, spec in DISCIPLINES.items()
            if spec['specialist'] and (spec['title'].search(jq) or spec['body'].search(jq))
        }
        uncovered_row = row_disc - prof_disc
        if uncovered_row:
            adj_s = min(raw_s, 0.20)
        else:
            adj_s = raw_s * (0.30 + 0.70 * domain_agreement_factor)
        adjusted_duty_scores.append(adj_s)
        if adj_s > best_duty_s and row.get('evidence_quote'):
            best_duty_s = adj_s
            best_duty_quote = jq[:220]
            best_duty_psrc = ev_src
            best_duty_pquote = str(row.get('evidence_quote') or '')[:220]

    structured_duty_cov = (
        sum(adjusted_duty_scores) / len(adjusted_duty_scores)
        if adjusted_duty_scores
        else None
    )

    # Bilingual work-product coverage across filtered job body vs cached profile products.
    job_product_quotes = analysis['product_quotes']
    job_products = job_product_quotes.keys()

    wp_cov: float | None = None
    if job_products and has_profile_evidence:
        wp_num = 0.0
        for wp in job_products:
            _, wp_discs = _WORK_PRODUCTS[wp]
            if wp in prof_products:
                is_wp_specialist = all(DISCIPLINES[d]['specialist'] for d in wp_discs if d in DISCIPLINES)
                if is_wp_specialist and not (wp_discs & prof_disc):
                    wp_num += 0.25
                else:
                    wp_num += 1.0 * (0.40 + 0.60 * domain_agreement_factor)
            elif wp_discs & prof_disc:
                wp_num += 0.30
        wp_cov = wp_num / len(job_products)
    elif job_products:
        wp_cov = 0.0

    if not has_profile_evidence:
        duty_coverage = 0.0
    else:
        if structured_duty_cov is not None and wp_cov is not None:
            core_duty = 0.55 * structured_duty_cov + 0.45 * wp_cov
        elif structured_duty_cov is not None:
            core_duty = structured_duty_cov
        elif wp_cov is not None:
            core_duty = wp_cov
        else:
            legacy_task_score = task_alignment['coverage']
            core_duty = legacy_task_score * domain_agreement_factor
        mod_lex = content_overlap * (0.35 + 0.65 * domain_agreement_factor)
        duty_coverage = 0.88 * core_duty + 0.12 * mod_lex

    duty_coverage = max(0.0, min(1.0, duty_coverage))
    if not best_duty_quote and job_products:
        matched_wps = sorted(job_products & prof_products)
        if matched_wps:
            best_duty_quote = job_product_quotes.get(matched_wps[0], '')
            best_duty_psrc = 'profile.cv_text'
            best_duty_pquote = ', '.join(matched_wps[:4])

    # -----------------------------------------------------------------------
    # 3. Substantive skill overlap (fast pre-filtered scan; incidental weight = 0)
    # -----------------------------------------------------------------------
    job_skill_weights: dict[str, float] = {}
    job_skill_quotes: dict[str, str] = {}
    job_skill_sources: dict[str, str] = {}
    job_skill_kinds: dict[str, str] = {}
    job_skill_required: set[str] = set()

    combined_job_text = analysis['combined_text']
    candidate_skills = analysis['candidate_skills']
    for skill, src, quote, kind, weight in analysis['skill_mentions']:
        if skill in GENERIC_SKILLS and src != 'title':
            weight *= 0.45 + 0.40 * domain_agreement_factor
        if weight > job_skill_weights.get(skill, 0.0):
            job_skill_weights[skill] = weight
            job_skill_quotes[skill] = quote
            job_skill_sources[skill] = src
            job_skill_kinds[skill] = kind
        if kind == 'required':
            job_skill_required.add(skill)

    # Match custom profile skills substantively against filtered posting text.
    # Keep the evidence shape consumed by the v1 job detail UI.
    custom_evidence: list[dict[str, Any]] = []
    for custom in custom_profile_skills:
        pat = _cached_pattern(custom)
        if not pat.search(combined_job_text):
            continue
        matched_custom = False
        if pat.search(title):
            job_skill_weights[custom] = max(job_skill_weights.get(custom, 0.0), 1.3)
            job_skill_quotes.setdefault(custom, title[:200])
            job_skill_sources.setdefault(custom, 'title')
            job_skill_kinds.setdefault(custom, 'required')
            job_skill_required.add(custom)
            profile_evidenced_skills.add(custom)
            custom_evidence.append({
                'name': custom,
                'source': 'title',
                'evidence': title[:200],
            })
            matched_custom = True
        if matched_custom:
            continue
        for src, sentence, scope in filtered_sentences:
            m = pat.search(sentence)
            if not m:
                continue
            kind = classify_skill_mention(sentence, m.start(), m.end(), scope)
            if kind == 'incidental':
                continue
            w = 1.0 if kind == 'required' else 0.75 if kind == 'duty' else 0.35
            job_skill_weights[custom] = max(job_skill_weights.get(custom, 0.0), w)
            job_skill_quotes.setdefault(custom, sentence[:200])
            job_skill_sources.setdefault(custom, src)
            job_skill_kinds.setdefault(custom, kind)
            if kind == 'required':
                job_skill_required.add(custom)
            profile_evidenced_skills.add(custom)
            custom_evidence.append({
                'name': custom,
                'source': src,
                'evidence': sentence[:200],
            })
            break

    matched_skills: list[str] = []
    missing_skills: list[str] = []
    skill_num = 0.0
    skill_den = 0.0
    for skill, w in sorted(job_skill_weights.items(), key=lambda kv: (-kv[1], kv[0])):
        skill_den += w
        if skill in profile_evidenced_skills:
            matched_skills.append(skill)
            credit = w * (domain_agreement_factor if skill in GENERIC_SKILLS else 1.0)
            skill_num += credit
        else:
            if w >= 0.7:
                missing_skills.append(skill)

    if not has_profile_evidence:
        skill_overlap = 0.0
    elif skill_den > 0:
        raw_ratio = skill_num / skill_den
        prior_strength = 0.8
        prior_val = 0.08 * domain_alignment
        skill_overlap = (skill_num + prior_strength * prior_val) / (skill_den + prior_strength)
        has_non_generic = any(sk not in GENERIC_SKILLS for sk in matched_skills)
        if len(matched_skills) >= 3 and raw_ratio >= 0.75 and (has_non_generic or domain_alignment >= 0.5):
            evidence_strength = skill_den / (skill_den + 0.20)
            skill_overlap = raw_ratio * evidence_strength
        skill_overlap *= 0.94 + 0.06 * content_overlap
    else:
        skill_overlap = 0.08 * domain_alignment + 0.12 * content_overlap
    skill_overlap = max(0.0, min(1.0, skill_overlap))

    # -----------------------------------------------------------------------
    # 4. Requirement coverage (deduplicated once via clause_key, skipping lang/visa)
    # -----------------------------------------------------------------------
    req_clauses_seen: set[str] = set()
    req_total = 0.0
    req_met = 0.0
    requirement_gaps: list[str] = []
    formal_prereq_gaps: list[dict[str, str]] = []
    formal_prereq_conditions: list[dict[str, Any]] = []
    requirement_observations: list[dict[str, Any]] = []
    seen_formal_keys: set[str] = set()

    edu_profile = pc['edu_profile']
    qual_rows = analysis['qualification_rows']

    for row in qual_rows:
        qtext = str(row.get('evidence') or row.get('quote') or row.get('value') or '').strip()
        qsource = str(row.get('source') or 'description')
        if not qtext or is_language_or_visa_clause(qtext) or is_company_experience_claim(qtext):
            continue
        if 'unspecified language level' in qtext.casefold():
            continue
        ckey = clause_key(qtext)
        if not ckey or ckey in req_clauses_seen:
            continue
        req_clauses_seen.add(ckey)

        req_total += 1.0
        arms = split_or_arms(qtext)
        arm_satisfied = False
        ev_src = ''
        ev_quote = ''
        is_degree = bool(DEGREE.search(qtext) or row.get('kind') == 'education')
        is_license = bool(_FORMAL_LICENSE_RE.search(qtext))

        for arm in arms:
            if DEGREE.search(arm):
                if evidence := education_evidence(row, edu_profile):
                    arm_satisfied = True
                    ev_src, ev_quote = evidence
                    break
            arm_skills = [
                sk for sk in candidate_skills
                if _SKILL_COMBINED[sk].search(arm)
            ]
            if arm_skills and any(sk in profile_evidenced_skills for sk in arm_skills):
                matched_arm_sk = [sk for sk in arm_skills if sk in profile_evidenced_skills]
                arm_satisfied = True
                ev_src = 'profile.skills'
                ev_quote = ', '.join(matched_arm_sk[:4])
                break
            arm_wps = [wp for wp, (pat, _) in _WORK_PRODUCTS.items() if pat.search(arm)]
            if arm_wps and any(wp in prof_products for wp in arm_wps):
                arm_satisfied = True
                ev_src = 'profile.cv_text'
                ev_quote = ', '.join(wp for wp in arm_wps if wp in prof_products)
                break
            arm_discs = [
                d for d, spec in DISCIPLINES.items()
                if spec['title'].search(arm) or spec['body'].search(arm)
            ]
            if arm_discs and any(d in prof_disc for d in arm_discs):
                arm_satisfied = True
                ev_src = 'profile.cv_text'
                ev_quote = ', '.join(d for d in arm_discs if d in prof_disc)
                break

        obs_kind = 'license' if is_license else 'education' if is_degree else str(row.get('kind') or 'qualification')
        if arm_satisfied:
            req_met += 1.0
            requirement_observations.append({
                'kind': obs_kind,
                'requirement': qtext[:200],
                'status': 'supported',
                'job_source': qsource,
                'job_quote': qtext[:240],
                'evidence_source': ev_src,
                'evidence_quote': ev_quote,
                'evidence_context': 'professional' if ev_src else 'unspecified',
                'explanation': f'Saved profile evidence aligns with requirement: {qtext[:140]}',
            })
            if (is_degree or is_license) and ckey not in seen_formal_keys:
                seen_formal_keys.add(ckey)
                formal_prereq_conditions.append({
                    'kind': 'license' if is_license else 'education',
                    'gate': 'qualification',
                    'status': 'pass',
                    'reason': f'Saved profile evidence covers stated {obs_kind}: {qtext[:160]}',
                    'detail': f'Saved profile evidence covers stated {obs_kind}: {qtext[:160]}',
                    'job_source': qsource,
                    'job_quote': qtext[:240],
                    'profile_source': ev_src,
                    'profile_quote': ev_quote,
                })
        else:
            requirement_gaps.append(qtext[:180])
            requirement_observations.append({
                'kind': obs_kind,
                'requirement': qtext[:200],
                'status': 'unknown',
                'job_source': qsource,
                'job_quote': qtext[:240],
                'evidence_source': '',
                'evidence_quote': '',
                'evidence_context': 'unspecified',
                'explanation': f'Required {obs_kind} is unverified in saved profile: {qtext[:140]}',
            })
            if (is_degree or is_license) and ckey not in seen_formal_keys:
                seen_formal_keys.add(ckey)
                fp_kind = 'license' if is_license else 'degree'
                formal_prereq_gaps.append({
                    'clause_key': ckey,
                    'quote': qtext[:200],
                    'kind': fp_kind,
                    'source': qsource,
                })
                formal_prereq_conditions.append({
                    'kind': 'license' if is_license else 'education',
                    'gate': 'qualification',
                    'status': 'unknown',
                    'reason': f'Required {fp_kind} is unverified: {qtext[:160]}',
                    'detail': f'Required {fp_kind} is unverified: {qtext[:160]}',
                    'job_source': qsource,
                    'job_quote': qtext[:240],
                    'profile_source': 'profile.education',
                    'profile_quote': str(profile.get('education') or '')[:140],
                })

    # Scan filtered requirements sentences for regulated licenses not caught by qualification_rows.
    for src, sentence, scope in filtered_sentences:
        if scope in {'optional', 'benefits'} or OPTIONAL.search(sentence):
            continue
        m_lic = _FORMAL_LICENSE_RE.search(sentence)
        if not m_lic:
            continue
        if requirement_polarity(sentence, m_lic.start(), m_lic.end()) != 'required':
            continue
        ckey = clause_key(sentence)
        if ckey in seen_formal_keys:
            continue
        lic_term = m_lic.group(0)
        if _cached_pattern(lic_term).search(profile_full_text):
            seen_formal_keys.add(ckey)
            formal_prereq_conditions.append({
                'kind': 'license',
                'gate': 'qualification',
                'status': 'pass',
                'reason': f'Profile references regulated credential ({lic_term}).',
                'detail': f'Profile references regulated credential ({lic_term}).',
                'job_source': src,
                'job_quote': sentence[:240],
                'profile_source': 'profile.cv_text',
                'profile_quote': lic_term,
            })
            continue
        seen_formal_keys.add(ckey)
        formal_prereq_gaps.append({
            'clause_key': ckey,
            'quote': sentence[:200],
            'kind': 'license',
            'source': src,
        })
        formal_prereq_conditions.append({
            'kind': 'license',
            'gate': 'qualification',
            'status': 'unknown',
            'reason': f'Required regulated license is unverified: {sentence[:160]}',
            'detail': f'Required regulated license is unverified: {sentence[:160]}',
            'job_source': src,
            'job_quote': sentence[:240],
            'profile_source': 'profile.cv_text',
            'profile_quote': '',
        })
        if ckey not in req_clauses_seen:
            req_clauses_seen.add(ckey)
            req_total += 1.0
            requirement_gaps.append(sentence[:180])
            requirement_observations.append({
                'kind': 'license',
                'requirement': sentence[:200],
                'status': 'unknown',
                'job_source': src,
                'job_quote': sentence[:240],
                'evidence_source': '',
                'evidence_quote': '',
                'evidence_context': 'unspecified',
                'explanation': f'Required regulated license is unverified: {sentence[:140]}',
            })

    # Include required substantive skills (each once) in requirement_coverage and observations.
    for skill in sorted(job_skill_required):
        skey = f'skill:{skill.casefold()}'
        if skey in req_clauses_seen:
            continue
        req_clauses_seen.add(skey)
        w = 0.6 if skill in GENERIC_SKILLS else 1.0
        req_total += w
        if skill in profile_evidenced_skills:
            req_met += w * (domain_agreement_factor if skill in GENERIC_SKILLS else 1.0)
            requirement_observations.append({
                'kind': 'skill',
                'requirement': skill,
                'status': 'supported',
                'job_source': job_skill_sources.get(skill, 'description'),
                'job_quote': job_skill_quotes.get(skill, skill)[:240],
                'evidence_source': 'profile.skills' if skill in profile_skills_norm else 'profile.cv_text',
                'evidence_quote': skill,
                'evidence_context': 'declared' if skill in profile_skills_norm else 'professional',
                'explanation': f'Required skill {skill} is evidenced in the saved profile.',
            })
        elif skill not in requirement_gaps:
            requirement_gaps.append(f'Missing required skill: {skill}')
            requirement_observations.append({
                'kind': 'skill',
                'requirement': skill,
                'status': 'unknown',
                'job_source': job_skill_sources.get(skill, 'description'),
                'job_quote': job_skill_quotes.get(skill, skill)[:240],
                'evidence_source': '',
                'evidence_quote': '',
                'evidence_context': 'unspecified',
                'explanation': f'Required skill {skill} was not found in saved profile evidence.',
            })

    # Detect title-only / non-substantive postings (Finding 6).
    substantive_word_count = analysis['substantive_word_count']
    is_title_only = not filtered_body or (
        substantive_word_count < 12
        and not adjusted_duty_scores
        and not job_products
        and req_total == 0
        and not job_skill_weights
    )
    extra_unknowns: list[str] = []
    if is_title_only:
        extra_unknowns.append(
            'Substantive responsibilities and qualification requirements are missing from this posting (title-only); verify role fit.'
        )
    if not has_profile_evidence:
        extra_unknowns.append(
            'Profile has no demonstrated CV, skill, or experience evidence; desired roles reflect preference only.'
        )

    if is_title_only or not has_profile_evidence:
        requirement_coverage = 0.0
        if is_title_only:
            duty_coverage = 0.0
            domain_alignment = 0.0 if not has_profile_evidence else min(domain_alignment, 0.25)
    elif req_total > 0:
        req_prior = ((domain_alignment + duty_coverage + skill_overlap) / 3.0) * domain_agreement_factor
        requirement_coverage = (req_met + 0.22 * req_prior) / (req_total + 0.22)
    else:
        requirement_coverage = 0.45 * max(domain_alignment, duty_coverage, skill_overlap) * domain_agreement_factor
    requirement_coverage = max(0.0, min(1.0, requirement_coverage))

    # -----------------------------------------------------------------------
    # 5. Role alignment
    # -----------------------------------------------------------------------
    role_alignment, role_detail, role_psrc, role_pquote = _compute_role_alignment(
        title, pc, job_title_disc, prof_disc
    )

    # -----------------------------------------------------------------------
    # 6. Seniority & tenure fit (continuous + bounded soft penalties)
    # -----------------------------------------------------------------------
    job_sen = detect_title_seniority(title)
    tenure_minimums = analysis['tenure_minimums']
    req_years, req_years_quote, _ = max(tenure_minimums, key=lambda row: row[0], default=(None, '', ''))
    req_years_quote = req_years_quote[:240]
    prof_sen = pc['prof_sen']
    prof_years = pc['prof_years']
    prof_sen_quote = pc['prof_sen_quote']

    requirement_observations.extend(
        {**row, 'job_source': source}
        for _, quote, source in tenure_minimums
        for row in _scoped_experience_rows(quote, prof_years)
    )

    if job_sen is None and req_years is not None:
        if req_years >= 8:
            job_sen = 4
        elif req_years >= 5:
            job_sen = 3
        elif req_years >= 2:
            job_sen = 2
        else:
            job_sen = 1

    _TIER_EXPECTED_YRS = {0: 0.2, 1: 1.0, 2: 3.5, 3: 6.5, 4: 9.5, 5: 12.5}
    sen_scores: list[float] = []
    seniority_gap_pen = 0.0
    seniority_gap_detail = ''
    if job_sen is not None:
        diff = job_sen - prof_sen
        if diff == 0:
            tier_s = 1.0
        elif abs(diff) == 1:
            tier_s = 0.75 if diff < 0 else 0.68
        elif diff >= 2:
            tier_s = max(0.10, 0.50 - 0.18 * (diff - 1))
            seniority_gap_pen = min(
                SOFT_PENALTIES['seniority_gap'],
                SOFT_PENALTIES['seniority_gap'] * min(1.0, (diff - 1) / 2.0),
            )
            seniority_gap_detail = f'Job seniority tier ({job_sen}) exceeds profile seniority tier ({prof_sen})'
        else:
            tier_s = max(0.12, 0.55 - 0.20 * (abs(diff) - 1))
            if job_sen == 0 and prof_sen >= 2:
                seniority_gap_pen = min(
                    SOFT_PENALTIES['seniority_gap'],
                    SOFT_PENALTIES['seniority_gap'] * 0.75,
                )
                seniority_gap_detail = 'Internship/student-level posting vs experienced profile'
        if prof_years is not None:
            exp_yrs = _TIER_EXPECTED_YRS.get(job_sen, 3.5)
            cont_s = math.exp(-abs(exp_yrs - prof_years) / 6.5)
            sen_scores.append(0.70 * tier_s + 0.30 * cont_s)
        else:
            sen_scores.append(tier_s)

    tenure_shortfall_pen = 0.0
    tenure_detail = ''
    if req_years is not None and prof_years is not None:
        shortfall = req_years - prof_years
        if shortfall <= 0:
            sen_scores.append(1.0)
        elif shortfall <= 1.0:
            sen_scores.append(max(0.75, 1.0 - 0.20 * shortfall))
        else:
            sen_scores.append(max(0.10, 1.0 - 0.15 * shortfall))
            frac = min(1.0, (shortfall - 1.0) / 5.0)
            tenure_shortfall_pen = round(SOFT_PENALTIES['tenure_shortfall_max'] * frac, 4)
            tenure_detail = (
                f'Posting asks for {req_years:g}y experience vs profile {prof_years:g}y '
                f'(shortfall {shortfall:g}y)'
            )

    if not has_profile_evidence:
        seniority_fit = 0.0
    else:
        raw_seniority_fit = sum(sen_scores) / len(sen_scores) if sen_scores else 0.50
        relevance_gate = max(domain_alignment, duty_coverage, skill_overlap)
        # Retain a gradient above moderate relevance instead of flattening
        # distinct evidence at a threshold.
        relevance_strength = -math.expm1(-4.0 * relevance_gate) / -math.expm1(-4.0)
        seniority_fit = raw_seniority_fit * (0.12 + 0.88 * relevance_strength)
        if is_title_only:
            seniority_fit = min(seniority_fit, 0.25)
    seniority_fit = max(0.0, min(1.0, seniority_fit))

    # -----------------------------------------------------------------------
    # 7. Semantic similarity (0.0 when absent; smooth monotone normalization)
    # -----------------------------------------------------------------------
    sem_sim = 0.0
    sem_detail = 'Semantic evidence not provided'
    sem_quote = ''
    if isinstance(semantic, dict):
        raw_sim = semantic.get('similarity')
        if raw_sim is None and semantic.get('score') is not None:
            raw_sim = float(semantic['score']) / 100.0
        if raw_sim is not None:
            raw_sim_f = float(raw_sim)
            alignment = semantic.get('alignment')
            if alignment is None:
                alignment = (raw_sim_f - 0.35) / 0.55
            sem_sim = max(0.0, min(1.0, float(alignment)))
            sem_detail = f'Semantic similarity {raw_sim_f:.3f} (normalized {sem_sim:.3f})'
            sem_quote = str(semantic.get('job_evidence') or semantic.get('job_quote') or '')[:180]

    # -----------------------------------------------------------------------
    # 8. Remaining bounded soft penalties
    # -----------------------------------------------------------------------
    soft_conflicts: list[str] = []
    penalties: list[dict[str, Any]] = []

    if tenure_shortfall_pen > 0:
        soft_conflicts.append(tenure_detail)
        penalties.append({
            'signal': 'penalty:tenure_shortfall',
            'amount': round(tenure_shortfall_pen, 4),
            'detail': tenure_detail,
            'job_quote': req_years_quote,
            'profile_source': 'profile.experience_years',
            'profile_quote': prof_sen_quote,
        })

    if seniority_gap_pen > 0:
        soft_conflicts.append(seniority_gap_detail)
        penalties.append({
            'signal': 'penalty:seniority_gap',
            'amount': round(seniority_gap_pen, 4),
            'detail': seniority_gap_detail,
            'job_quote': title[:180],
            'profile_source': 'profile.seniority',
            'profile_quote': prof_sen_quote,
        })

    # Work style preference mismatch (only when NOT already a hard fail).
    pref_styles: frozenset[str] = pc['pref_styles']
    job_style = str(
        job.get('work_arrangement')
        or ('remote' if job.get('is_remote') else enriched.get('work_style') or '')
    ).strip().casefold()
    if pref_styles and job_style and job_style not in pref_styles and gate_result['location']['status'] != 'fail':
        pen_amt = SOFT_PENALTIES['work_style_mismatch']
        if job_style == 'hybrid' and ('remote' in pref_styles or 'onsite' in pref_styles):
            pen_amt *= 0.5
        detail = f'Work arrangement ({job_style}) differs from preferred {", ".join(sorted(pref_styles))}'
        soft_conflicts.append(detail)
        penalties.append({
            'signal': 'penalty:work_style',
            'amount': round(pen_amt, 4),
            'detail': detail,
            'job_quote': f'work_arrangement={job_style}',
            'profile_source': 'profile.work_styles',
            'profile_quote': ', '.join(sorted(pref_styles)),
        })

    # Employment type preference mismatch.
    pref_emp: frozenset[str] = pc['pref_emp']
    job_emp = str(
        job.get('employment_type') or enriched.get('employment_type') or ''
    ).strip().casefold().replace('_', '-').replace(' ', '-')
    if not job_emp and job_sen == 0:
        job_emp = 'internship'
    if pref_emp and job_emp and job_emp not in pref_emp:
        equiv = {'full-time', 'fulltime', 'permanent', 'vollzeit'}
        if not (job_emp in equiv and (pref_emp & equiv)):
            pen_amt = SOFT_PENALTIES['employment_type_mismatch']
            detail = f'Employment type ({job_emp}) differs from preferred {", ".join(sorted(pref_emp))}'
            soft_conflicts.append(detail)
            penalties.append({
                'signal': 'penalty:employment_type',
                'amount': round(pen_amt, 4),
                'detail': detail,
                'job_quote': f'employment_type={job_emp}',
                'profile_source': 'profile.employment_types',
                'profile_quote': ', '.join(sorted(pref_emp)),
            })

    # Unverified formal prerequisites (licenses/degrees, each counted once, bounded).
    if formal_prereq_gaps:
        has_license = any(g['kind'] == 'license' for g in formal_prereq_gaps)
        raw_fp = (
            SOFT_PENALTIES['formal_prerequisite_max']
            if has_license
            else min(SOFT_PENALTIES['formal_prerequisite_max'] * 0.6, 2.5 * len(formal_prereq_gaps))
        )
        first_fp = formal_prereq_gaps[0]
        detail = f'Unverified formal prerequisite ({first_fp["kind"]}): {first_fp["quote"][:140]}'
        soft_conflicts.append(detail)
        penalties.append({
            'signal': 'penalty:formal_prerequisite',
            'amount': round(raw_fp, 4),
            'detail': detail,
            'job_quote': first_fp['quote'],
            'profile_source': 'profile.education',
            'profile_quote': str(profile.get('education') or '')[:120],
        })

    # Two raw points at the maximum calibration slope move the rounded score
    # by at most five points, including when both flags are present.
    lang_visa_flags = [
        f for f in gate_result['flags']
        if f.get('gate') in {'language', 'eligibility'}
    ]
    if lang_visa_flags:
        lv_max = min(5.0, float(SOFT_PENALTIES['language_visa_flag_max']))
        lv_amt = min(lv_max, 0.75 * lv_max if len(lang_visa_flags) == 1 else lv_max)
        first_lv = lang_visa_flags[0]
        penalties.append({
            'signal': 'penalty:language_visa_flag',
            'amount': round(lv_amt, 4),
            'detail': f'Language/work-authorization verification flag ({len(lang_visa_flags)}): {first_lv["detail"][:140]}',
            'job_quote': first_lv.get('job_quote', ''),
            'profile_source': first_lv.get('profile_source', ''),
            'profile_quote': first_lv.get('profile_quote', ''),
        })

    # Enforce total_soft_penalty_cap proportionally so every ledger row stays transparent.
    total_pen = sum(p['amount'] for p in penalties)
    cap = float(SOFT_PENALTIES['total_soft_penalty_cap'])
    if total_pen > cap and total_pen > 0:
        scale = cap / total_pen
        for p in penalties:
            p['amount'] = round(p['amount'] * scale, 4)

    features: dict[str, float] = {
        'duty_coverage': round(duty_coverage, 4),
        'requirement_coverage': round(requirement_coverage, 4),
        'skill_overlap': round(skill_overlap, 4),
        'role_alignment': round(role_alignment, 4),
        'domain_alignment': round(max(0.0, min(1.0, domain_alignment)), 4),
        'seniority_fit': round(seniority_fit, 4),
        'semantic_similarity': round(sem_sim, 4),
        'domain_mismatch': round(max(0.0, min(1.0, domain_mismatch)), 4),
    }

    requirement_citation = next(
        (row for row in requirement_observations if row['status'] == 'supported'),
        next(iter(requirement_observations), {}),
    )
    feature_citations: dict[str, dict[str, str]] = {
        'duty_coverage': {
            'detail': (
                f'Domain-aware duty coverage {features["duty_coverage"]:.2f} '
                f'({len(adjusted_duty_scores)} duties, {len(job_products & prof_products)}/{len(job_products)} work products)'
            ),
            'job_quote': best_duty_quote,
            'profile_source': best_duty_psrc,
            'profile_quote': best_duty_pquote,
        },
        'requirement_coverage': {
            'detail': f'Requirement coverage {features["requirement_coverage"]:.2f} ({req_met:.1f}/{req_total:.1f} weighted clauses)',
            'job_quote': requirement_citation.get('job_quote', ''),
            'profile_source': requirement_citation.get('evidence_source', ''),
            'profile_quote': requirement_citation.get('evidence_quote', ''),
        },
        'skill_overlap': {
            'detail': f'Substantive skill overlap {features["skill_overlap"]:.2f} ({len(matched_skills)} matched, {len(missing_skills)} missing)',
            'job_quote': job_skill_quotes.get(matched_skills[0], '') if matched_skills else (job_skill_quotes.get(missing_skills[0], '') if missing_skills else ''),
            'profile_source': 'profile.skills' if matched_skills else '',
            'profile_quote': ', '.join(matched_skills[:6]),
        },
        'role_alignment': {
            'detail': role_detail,
            'job_quote': title[:180],
            'profile_source': role_psrc,
            'profile_quote': role_pquote,
        },
        'domain_alignment': {
            'detail': (
                f'Discipline alignment {features["domain_alignment"]:.2f} '
                f'(job: {", ".join(sorted(job_disc)) or "general"}; '
                f'profile: {", ".join(sorted(prof_disc)) or "general"})'
            ),
            'job_quote': title[:180],
            'profile_source': 'profile.cv_text' if prof_disc else '',
            'profile_quote': ', '.join(sorted(prof_disc)[:5]),
        },
        'seniority_fit': {
            'detail': f'Seniority/tenure fit {features["seniority_fit"]:.2f} (job tier={job_sen}, req_years={req_years}, profile tier={prof_sen}, years={prof_years})',
            'job_quote': req_years_quote or title[:180],
            'profile_source': 'profile.experience_years' if prof_years is not None else 'profile.seniority',
            'profile_quote': prof_sen_quote,
        },
        'semantic_similarity': {
            'detail': sem_detail,
            'job_quote': sem_quote,
            'profile_source': 'profile.cv_text' if sem_sim > 0 else '',
            'profile_quote': '',
        },
        'domain_mismatch': {
            'detail': (
                f'Uncovered specialist disciplines: {", ".join(sorted(uncovered_specialist))}'
                if uncovered_specialist
                else 'No uncovered specialist discipline mismatch'
            ),
            'job_quote': title[:180] if uncovered_specialist else '',
            'profile_source': 'profile.cv_text' if uncovered_specialist else '',
            'profile_quote': ', '.join(sorted(prof_disc)[:5]) if uncovered_specialist else '',
        },
    }

    content_match_matches = [
        {
            'skill': sk,
            'profile_source': 'skills' if sk in profile_skills_norm else 'cv_text',
            'weight': round(float(job_skill_weights.get(sk, 1.0)), 4),
            'source': job_skill_sources.get(sk, 'description'),
            'evidence': job_skill_quotes.get(sk, ''),
            'kind': job_skill_kinds.get(sk, 'required'),
        }
        for sk in matched_skills
    ]

    return {
        'features': features,
        'feature_citations': feature_citations,
        'penalties': penalties,
        'matched_skills': matched_skills,
        'missing_skills': missing_skills,
        'requirement_gaps': requirement_gaps[:12],
        'soft_conflicts': soft_conflicts,
        'extra_unknowns': extra_unknowns,
        'duty_evidence': duty_evidence,
        'task_alignment': task_alignment,
        'custom_skill_evidence': custom_evidence,
        'profile_skill_context': {
            sk: dict(pc['skill_contexts'][sk]) if sk in pc['skill_contexts'] else {
                'context': 'declared', 'scope_verified': False, 'source': 'profile.skills', 'quote': sk,
            }
            for sk in matched_skills
        },
        'formal_prereq_gaps': formal_prereq_gaps,
        'formal_prereq_conditions': formal_prereq_conditions,
        'requirement_observations': requirement_observations,
        'content_match_matches': content_match_matches,
        'job_disciplines': sorted(job_disc),
        'profile_disciplines': sorted(prof_disc),
        'uncovered_specialist': sorted(uncovered_specialist),
        'is_title_only': is_title_only,
    }
