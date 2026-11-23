"""Static bilingual skill, role, discipline, work-product, and seniority taxonomies for v2."""
from __future__ import annotations

from functools import lru_cache
import re
from typing import Any

from hiring_scraper.matching import ROLE_GROUPS, SKILLS, _pattern


@lru_cache(maxsize=2048)
def _cached_pattern(term: str) -> re.Pattern[str]:
    return _pattern(term)


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
# Seniority & tenure extraction (bilingual, OR-arm aware, company-boast safe)
# ---------------------------------------------------------------------------

_SENIORITY_LEVELS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (5, re.compile(r'\b(?:c-level|ceo|cto|cfo|coo|cpo|cmo|vp\b|vice\s+president|svp|evp|director|direktor\w*|head\s+of|bereichsleit\w*|abteilungsleit\w*|geschäftsführ\w*|geschäftsleit\w*|vorstand)\b', re.I)),
    (4, re.compile(r'\b(?:principal|staff\s+(?:engineer|designer|product)|team\s+lead|tech\s+lead|teamleit\w*|gruppenleit\w*|fachliche\s+leitung|fachleitung|lead\s+(?:engineer|developer|designer|product|manager|analyst|architect)|architect|[\wäöüß-]*architekt\w*)\b', re.I)),
    (3, re.compile(r'\b(?:senior|sr\.?\b|erfahrene[rnms]?\b|experienced\s+(?:engineer|developer|manager|specialist|consultant|analyst))\b', re.I)),
    (2, re.compile(
        r'\b(?:mid[- ]?level|professional|regular|specialist|manager|engineer|developer|analyst|'
        r'coordinator|consultant|referent\w*|berater\w*|spezialist\w*|'
        r'[\wäöüß-]*(?:manager\w*|engineer\w*|developer\w*|entwickler\w*|ingenieur\w*|analyst\w*|'
        r'koordinator\w*|coordinator\w*|berater\w*|consultant\w*|spezialist\w*|specialist\w*|'
        r'referent\w*|sachbearbeiter\w*|administrator\w*|controller\w*|techniker\w*|'
        r'fachkraft|fachkräfte|wissenschaftler\w*|konstrukteur\w*|redakteur\w*|planer\w*|'
        r'designer\w*|betreuer\w*|kaufmann|kauffrau|kaufleute|disponent\w*|buchhalter\w*|prüfer\w*))\b',
        re.I,
    )),
    (1, re.compile(r'\b(?:junior|jr\.?\b|entry[- ]?level|graduate|trainee|berufseinsteiger\w*|einsteiger\w*|nachwuchskraft|nachwuchskräfte|absolvent\w*|associate|assistant|assistent\w*|assistenz)\b', re.I)),
    (0, re.compile(r'\b(?:intern|internship|praktikant\w*|praktikum|working\s+student|werkstudent\w*|studentische\s+hilfskraft|auszubildend\w*|azubi|apprentice|dual\s+student|duales\s+studium)\b', re.I)),
)



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


_ROLE_GENDER_RE = re.compile(
    r'\((?:[mwdfhx]\s*[/|&]\s*){1,3}[mwdfhx]\)|\((?:all genders|gn|divers)\)',
    re.I,
)
_ROLE_TOK_RE = re.compile(r'[a-zäöüß0-9+/#&-]{2,}')



_ROLE_GROUP_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(
        r'(?<![\w+#])(?:' + '|'.join(re.escape(term) for term in group) + r')(?![\w+#])',
        re.I,
    )
    for group in ALL_ROLE_GROUPS
)
