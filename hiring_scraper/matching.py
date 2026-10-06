"""Versioned, evidence-backed job signals and an explainable local matching baseline.

Scores express overlap with a user's interests, never a probability of being hired.
Skill mentions are not automatically treated as mandatory qualifications.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from hiring_scraper.pages import Document

VERSION = 'rules-v4'
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
    'Project management': ['project management', 'projektmanagement', 'project coordination', 'projektkoordination', 'plan projects', 'coordinate projects'],
    'Requirements gathering': ['requirements gathering', 'requirements analysis', 'anforderungsanalyse', 'anforderungsmanagement'],
    'Project coordination': ['project coordination', 'projektkoordination'],
    'Product development': ['product development', 'produktentwicklung'],
    'Stakeholder coordination': ['stakeholder coordination', 'stakeholder management', 'stakeholderkommunikation', 'coordinate stakeholders'],
    'Process improvement': ['process improvement', 'prozessoptimierung', 'prozessverbesserung'],
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
     'projektmanagerin', 'projektmanagement', 'projektleitung'],
    ['product development', 'product manager', 'product management', 'product operations',
     'produktentwicklung', 'produktentwickler', 'produktentwicklerin', 'produktmanager', 'produktmanagerin'],
    ['innovation engineer', 'development engineer', 'innovationsingenieur', 'innovationsingenieurin',
     'entwicklungsingenieur', 'entwicklungsingenieurin', 'prototyping engineer'],
    ['learning and development', 'l&d coordinator', 'training coordinator', 'personalentwicklung',
     'weiterbildung', 'schulungskoordinator', 'schulungskoordinatorin'],
    ['process improvement', 'quality coordinator', 'prozessoptimierung', 'prozessverbesserung', 'qualitätskoordination'],
    ['program coordinator', 'programme coordinator', 'international partnerships', 'executive assistant',
     'programmkoordinator', 'programmkoordinatorin', 'internationale partnerschaften', 'assistenz der geschäftsführung'],
    ['customer service', 'customer operations', 'kundenservice', 'kundenbetreuung'],
    ['business analyst', 'business analysis', 'requirements analyst', 'business analystin',
     'business analyse', 'anforderungsmanager', 'anforderungsmanagerin'],
    ['cad designer', 'cad konstrukteur', 'cad konstrukteurin', 'konstrukteur', 'konstrukteurin'],
]
OPTIONAL = re.compile(r'nice.to.have|optional|preferred|idealerweise|wünschenswert|bevorzugt|von vorteil|a plus|not required|nicht erforderlich', re.I)
REQUIREMENT = re.compile(r'\b(required|requirement|requirements|your profile|your qualifications|must have|must possess|you need|ihr profil|dein profil|anforderungen|was sie mitbringen|was du mitbringst|wir erwarten|voraussetzung|erforderlich|zwingend|pflicht|mindestens|at least|minimum)\b', re.I)
NON_REQUIREMENT = re.compile(r'\b(we have|our company|our team|we offer|benefits|perks|you receive|you get|you can take|we provide you with|du erhältst|sie erhalten|wir bieten|unser unternehmen|unsere firma|seit \d{4}|common among our clients|spoken by our clients|our clients speak|our customers speak|our team speaks)\b', re.I)
CEFR_RANK = {'A1': 1, 'A2': 2, 'B1': 3, 'B2': 4, 'C1': 5, 'C2': 6, 'NATIVE': 7}


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


def _pattern(term: str):
    return re.compile(r'(?<![\w+#])' + re.escape(term) + r'(?![\w+#])', re.I)


def _evidence(value, text, match, source):
    return {'value': value, 'source': source,
            'evidence': text[max(0, match.start()-50):min(len(text), match.end()+70)]}


def _mention_kind(text: str, match, source: str) -> str:
    if source == 'title':
        return 'title'
    left = max(text.rfind('.', 0, match.start()), text.rfind(';', 0, match.start())) + 1
    ends = [position for position in (text.find('.', match.end()), text.find(';', match.end())) if position >= 0]
    sentence = text[left:min(ends) if ends else len(text)]
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


def _requirement_clauses(sentence: str) -> list[str]:
    """Keep shared wording together, but separate independently qualified clauses."""
    parts = re.split(r'\b(?:and|und|but|aber|jedoch)\b|,\s*', sentence, flags=re.I)
    clauses = []
    current = parts[0].strip()
    for part in parts[1:]:
        part = part.strip()
        if (OPTIONAL.search(current) or REQUIREMENT.search(current)) and (OPTIONAL.search(part) or REQUIREMENT.search(part)):
            clauses.append(current)
            current = part
        else:
            current += ' and ' + part
    clauses.append(current)
    return clauses


def _required_context(sentence: str, *, explicit: bool = False) -> bool:
    if OPTIONAL.search(sentence) or NON_REQUIREMENT.search(sentence):
        return False
    return explicit or bool(REQUIREMENT.search(sentence))


def _job_requirements(texts: list[tuple[str, str]]) -> tuple[dict | None, list[dict], list[dict]]:
    """Extract stated requirements from the sentence containing the evidence."""
    experience = None
    languages = []
    qualifications = []
    seen_qualifications = set()
    experience_pattern = re.compile(
        r'(?:(?:at least|minimum|min\.?|mindestens)\s*(\d{1,2})\s*(?:years?|jahren?)|'
        r'(\d{1,2})\+\s*years?|'
        r'(\d{1,2})\s*(?:years?|jahre?)\s+(?:of\s+)?[\w -]{0,55}?(?:experience|erfahrung))', re.I)
    language_patterns = {
        'German': re.compile(r'\b(?:german|deutsch(?:kenntnisse)?)\b', re.I),
        'English': re.compile(r'\b(?:english|englisch(?:kenntnisse)?)\b', re.I),
        'French': re.compile(r'\b(?:french|französisch(?:kenntnisse)?)\b', re.I),
        'Spanish': re.compile(r'\b(?:spanish|spanisch(?:kenntnisse)?)\b', re.I),
    }
    fluency = re.compile(r'\b(?:fluent|fließende[nrsm]?|fliessende[nrsm]?|verhandlungssichere[nrsm]?)\s+(?:german|deutsch(?:kenntnisse)?|english|englisch(?:kenntnisse)?|french|französisch(?:kenntnisse)?|spanish|spanisch(?:kenntnisse)?)\b', re.I)
    qualification = re.compile(r'\b(?:PMP|PRINCE2|Scrum|degree|abschluss|certification|zertifizier(?:ung|t)|medical devices?|medizintechnik|PV|photovoltaik|photovoltaics?|solar|electrical|elektrotechnik|construction|bauwesen|bauleitung)\b', re.I)
    for source, text in texts:
        if source == 'title':
            continue
        requirement_section = False
        for sentence in _sentences(text):
            if len(sentence) > 500:
                continue
            if re.match(r'^(?:ihr|dein|your)\s+profil\b|^\b(?:requirements|anforderungen|qualifications)\b', sentence, re.I):
                requirement_section = True
            elif re.match(r'^(?:ihr|ihre|dein|deine|your|unsere)\s+aufgaben\b|^\b(?:responsibilities|benefits|wir bieten|we offer)\b', sentence, re.I):
                requirement_section = False
            for clause in _requirement_clauses(sentence):
                for exp in experience_pattern.finditer(clause):
                    minimum = bool(re.search(r'at least|minimum|min\.?|mindestens|\+\s*years?', exp.group(), re.I))
                    if experience is None and _required_context(clause, explicit=requirement_section or minimum):
                        experience = _evidence(int(next(group for group in exp.groups() if group)), clause, exp, source)
                language_hits = sorted(((name, hit) for name, pattern in language_patterns.items()
                                        if (hit := pattern.search(clause))), key=lambda pair: pair[1].start())
                for index, (language, found) in enumerate(language_hits):
                    next_start = language_hits[index + 1][1].start() if index + 1 < len(language_hits) else len(clause)
                    previous_end = language_hits[index - 1][1].end() if index else 0
                    after = clause[found.end():next_start]
                    before = clause[previous_end:found.start()]
                    level = re.search(r'\b(?:A1|A2|B1|B2|C1|C2)\b', after[:28], re.I)
                    if level and re.search(r'\b(?:and|und|or|oder)\b', after[:level.start()], re.I):
                        level = None
                    if not level:
                        level = re.search(r'\b(?:A1|A2|B1|B2|C1|C2)\b\s*$', before[-15:], re.I)
                    if not (level or fluency.search(clause)) or not _required_context(clause, explicit=requirement_section or bool(fluency.search(clause))):
                        continue
                    if any(row['value'] == language for row in languages):
                        continue
                    row = _evidence(language, clause, found, source)
                    row['level'] = level.group().upper() if level else None
                    row['kind'] = 'explicit_cefr' if level else 'fluency'
                    languages.append(row)
                if _required_context(clause, explicit=requirement_section):
                    for found in qualification.finditer(clause):
                        key = (source, found.group().casefold())
                        if key not in seen_qualifications:
                            qualifications.append({**_evidence(found.group(), clause, found, source), 'kind': 'qualification'})
                            seen_qualifications.add(key)
    return experience, languages, qualifications


def enrich_job(job: dict) -> dict:
    raw = job.get('raw_metadata') or {}
    source_hash = hashlib.sha256(json.dumps(job, sort_keys=True, default=str).encode()).hexdigest()
    texts = [('title', plain_text(job.get('title'))), ('description', plain_text(job.get('description')))]
    detail_requirements = raw.get('detail_requirements') or {}
    for prefix, values in [('structured', raw), ('detail', detail_requirements)]:
        for field in ('skills', 'qualifications', 'experienceRequirements', 'educationRequirements'):
            value = values.get(field)
            if value:
                if not isinstance(value, str):
                    value = json.dumps(value, ensure_ascii=False)
                texts.append((prefix + '.' + field, plain_text(value)))
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
    experience, languages, requirements = _job_requirements(texts)
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
            'employment_type': employment,
            'employment_types': employment_types,
            'work_style': arrangement, 'description_chars': len(description),
            'quality': 'limited' if len(description) < 350 else 'description_available', 'warnings': warnings}


def _role_match(role: str, title: str) -> bool:
    role = role.strip().casefold()
    specific_roles = {'backend': ['backend','back-end','back end','full stack','fullstack'],
                      'frontend': ['frontend','front-end','front end','full stack','fullstack']}
    for focus, aliases in specific_roles.items():
        if _pattern(focus).search(role):
            return any(_pattern(alias).search(title) for alias in aliases)
    if _pattern(role).search(title):
        return True
    return any(any(_pattern(alias).search(role) for alias in group) and
               any(_pattern(alias).search(title) for alias in group) for group in ROLE_GROUPS)


def match_job(job: dict, profile: dict, enrichment: dict | None = None) -> dict:
    enriched = enrichment or enrich_job(job)
    profile_skills = normalize_skills(profile.get('skills', []))
    job_skills = [row['name'] for row in enriched['skills']]
    # Explicit user skills outside the small vocabulary still get literal matching.
    text = plain_text(str(job.get('title') or '') + ' ' + str(job.get('description') or ''))
    raw = job.get('raw_metadata') or {}
    custom_texts = [('description', text)]
    for prefix, data in [('structured',raw), ('detail',raw.get('detail_requirements') or {})]:
        for key in ('skills','qualifications'):
            if data.get(key):
                value = data[key] if isinstance(data[key],str) else json.dumps(data[key],ensure_ascii=False)
                custom_texts.append((prefix + '.' + key,plain_text(value)))
    custom_evidence = []
    for skill in profile_skills:
        if skill not in SKILLS:
            for source, evidence_text in custom_texts:
                found = _pattern(skill).search(evidence_text)
                if found:
                    job_skills.append(skill)
                    custom_evidence.append({**_evidence(skill,evidence_text,found,source), 'name':skill,
                                            'kind': _mention_kind(evidence_text, found, source)})
                    break
    matched = [name for name in profile_skills if name in job_skills]
    missing = [name for name in job_skills if name not in profile_skills]
    title = plain_text(job.get('title'))
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
    if experience and profile.get('experience_years') is None:
        unknowns.append('Profile experience not provided')
    if experience and profile.get('experience_years') is not None and experience['value'] > profile['experience_years']:
        conflicts.append(f"Requires at least {experience['value']:g} years; profile has {profile['experience_years']:g}")
    language_levels = {name.casefold(): str(level).upper() for name, level in (profile.get('language_levels') or {}).items()}
    known_languages = {x.casefold() for x in profile.get('languages', [])} | set(language_levels)
    if enriched['languages'] and not known_languages:
        unknowns.append('Profile languages not provided')
    for language in enriched['languages']:
        name = language['value']
        level = language.get('level')
        candidate_level = language_levels.get(name.casefold())
        if known_languages and name.casefold() not in known_languages:
            conflicts.append('Language requested: ' + language['value'])
        elif level and candidate_level in CEFR_RANK and CEFR_RANK[candidate_level] < CEFR_RANK[level]:
            conflicts.append(f'{name} {level} required; profile has {candidate_level}')
        elif level and not candidate_level:
            unknowns.append(f'{name} {level} required; profile level not provided')
        elif not level and name.casefold() in known_languages:
            unknowns.append(f'{name} fluency requested; exact level not stated')
    requirement_gaps = []
    evidence_by_skill = {name.casefold(): row for name, row in (profile.get('skill_evidence') or {}).items()}
    for requirement in enriched.get('requirements', []):
        value = str(requirement['value'])
        evidence = evidence_by_skill.get(value.casefold())
        if not evidence and value.casefold() in {skill.casefold() for skill in profile_skills}:
            continue
        if evidence and str(evidence.get('context', '')).casefold() == 'professional':
            continue
        gap = f"Verify requirement: {requirement['evidence']}"
        requirement_gaps.append(gap)
        unknowns.append(gap)
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
    score = min(100, round(score))
    # A title-only listing cannot imply a strong fit, even with a matching skill in its title.
    if enriched['quality'] == 'limited':
        score = min(score, 60)
    if requirement_gaps:
        score = min(score, 60)
    if secondary_roles and not roles:
        score = min(score, 59)
    fit_tier = ('unlikely' if conflicts or score < 30 else
                'recommended' if score >= 65 and enriched['quality'] != 'limited' and not requirement_gaps else
                'possible')
    return {'score': score, 'eligible': not conflicts, 'uncertain': bool(unknowns),
            'fit_tier': fit_tier, 'requirement_gaps': requirement_gaps,
            'matched_skills': matched, 'missing_skills': missing, 'reasons': reasons,
            'custom_skill_evidence': custom_evidence,
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
    description = plain_text(posting.get('description'))
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
            return ' '.join(narrative(child) if isinstance(child, _Element) else child for child in element.children)
        text = ' '.join(narrative(node).split())
        start = text.find(headings[0].text())
        if start < 0:
            continue
        text = text[start:]
        text = re.split(r'other jobs|related jobs|weitere stellen|nicht der richtige job|das ist nicht die passende stelle', text, maxsplit=1, flags=re.I)[0].strip()
        if len(text) < 350 or not re.search(r'your (?:responsibilities|profile|tasks)|responsibilities|qualifications|dein(?:e)? (?:profil|aufgaben)|ihr(?:e)? (?:profil|aufgaben)|anforderungen',text,re.I):
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
