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

VERSION = 'rules-v1'
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
    'Project management': ['project management', 'projektmanagement'],
    'Scrum': ['scrum'], 'Product management': ['product management', 'produktmanagement'],
    'Customer service': ['customer service', 'kundenservice', 'kundenbetreuung'],
    'Sales': ['sales', 'vertrieb'], 'Marketing': ['marketing'], 'SEO': ['seo', 'suchmaschinenoptimierung'],
    'Recruiting': ['recruiting', 'recruitment', 'personalbeschaffung'],
    'CAD': ['cad', 'autocad'], 'SolidWorks': ['solidworks'], 'Electrical engineering': ['electrical engineering', 'elektrotechnik'],
    'Nursing': ['nursing', 'krankenpflege', 'altenpflege'], 'Logistics': ['logistics', 'logistik'],
}
ROLE_GROUPS = [
    ['software developer', 'software engineer', 'softwareentwickler', 'entwickler', 'developer', 'backend', 'frontend', 'full stack', 'fullstack'],
    ['data scientist', 'data analyst', 'datenanalyst', 'data science', 'machine learning'],
    ['devops', 'platform engineer', 'cloud engineer', 'systemadministrator', 'system administrator'],
    ['accountant', 'accounting', 'buchhalter', 'buchhaltung', 'finance', 'finanz', 'bilanzbuchhalter'],
    ['sales', 'vertrieb', 'account manager'], ['marketing', 'seo', 'content manager'],
    ['nursing', 'nurse', 'pflege', 'pflegefachkraft'], ['logistics', 'logistik', 'lager'],
]
OPTIONAL = re.compile(r'nice.to.have|optional|idealerweise|wünschenswert|von vorteil|a plus|not required|nicht erforderlich', re.I)


def plain_text(value) -> str:
    if not isinstance(value, str):
        return ''
    doc = Document()
    doc.feed(value[:120000])
    return ' '.join(' '.join(doc.text).split())


def _pattern(term: str):
    return re.compile(r'(?<![\w+#])' + re.escape(term) + r'(?![\w+#])', re.I)


def _evidence(value, text, match, source):
    return {'value': value, 'source': source,
            'evidence': text[max(0, match.start()-50):min(len(text), match.end()+70)]}


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
        for source, text in texts:
            match = next((found for alias in aliases if (found := _pattern(alias).search(text))), None)
            if match:
                evidence = {**_evidence(name, text, match, source), 'name': name, 'kind': 'mentioned'}
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
    experience = None
    exp_pattern = re.compile(r'(?:mindestens|at least|minimum|min\.?)\s*(\d{1,2})\s*(?:years?|jahren?)\s*(?:of\s+)?(?:relevant\s+)?(?:berufs)?(?:experience|erfahrung)|(\d{1,2})\+\s*years?\s*(?:of\s+)?experience', re.I)
    for source, text in texts:
        match = exp_pattern.search(text)
        if match:
            experience = _evidence(int(match.group(1) or match.group(2)), text, match, source)
            break
    structured_exp = detail_requirements.get('experienceRequirements') or raw.get('experienceRequirements')
    if isinstance(structured_exp, dict):
        try:
            months = float(structured_exp.get('monthsOfExperience'))
            if 0 <= months <= 600:
                experience = {'value': months / 12, 'source': 'detail.experienceRequirements' if detail_requirements.get('experienceRequirements') else 'structured.experienceRequirements', 'evidence': json.dumps(structured_exp)}
        except (ValueError, TypeError):
            pass
    languages = []
    description = texts[1][1]
    for language, aliases in {'German': ['German', 'Deutsch', 'Deutschkenntnisse'], 'English': ['English', 'Englisch', 'Englischkenntnisse']}.items():
        regex = re.compile(r'(?:fluent|fließende[nr]?|fliessende[nr]?|verhandlungssichere[nr]?)\s+(?:' + '|'.join(aliases) + r')|(?:' + '|'.join(aliases) + r')\s*(?:at\s+)?(?:C[12]|B[12])\b', re.I)
        match = regex.search(description)
        if match and not OPTIONAL.search(description[max(0, match.start()-35):match.end()+45]):
            languages.append(_evidence(language, description, match, 'description'))
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
            'experience_years': experience, 'languages': languages, 'employment_type': employment,
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
                    custom_evidence.append({**_evidence(skill,evidence_text,found,source), 'name':skill})
                    break
    matched = [name for name in profile_skills if name in job_skills]
    missing = [name for name in job_skills if name not in profile_skills]
    roles = [role for role in profile.get('desired_roles', []) if _role_match(role, plain_text(job.get('title')))]
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
    known_languages = [x.casefold() for x in profile.get('languages', [])]
    if enriched['languages'] and not known_languages:
        unknowns.append('Profile languages not provided')
    for language in enriched['languages']:
        if known_languages and language['value'].casefold() not in known_languages:
            conflicts.append('Language requested: ' + language['value'])
    for term in profile.get('excluded_terms', []):
        if _pattern(term).search(plain_text(job.get('title'))):
            conflicts.append('Excluded title term: ' + term)
    if enriched['quality'] == 'limited':
        unknowns.append('Full job description needs checking')
    if profile_skills and not job_skills:
        unknowns.append('Skills not stated or not recognized')
    score = 0.0
    if profile_skills and matched:
        score += (55 if profile.get('desired_roles') else 100) * 2 * len(matched) / (len(profile_skills) + len(job_skills))
        reasons.append('Skills mentioned: ' + ', '.join(matched))
    if roles:
        score += 45 if profile_skills else 100
        reasons.extend('Role matches ' + role for role in roles)
    score = min(100, round(score))
    # A title-only listing cannot imply a strong fit, even with a matching skill in its title.
    if enriched['quality'] == 'limited':
        score = min(score, 60)
    return {'score': score, 'eligible': not conflicts, 'uncertain': bool(unknowns),
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
