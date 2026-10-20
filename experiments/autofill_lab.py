"""Exploratory autofill benchmark. No production imports of this module.

Run from the repository root:
  .venv/bin/python experiments/autofill_lab.py
  .venv/bin/python experiments/autofill_lab.py --embeddings

Uses fictional data only. The optional embedding experiment downloads the
project's pinned multilingual E5 model if it is not cached.
"""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from html import escape
import json
from pathlib import Path
import re
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))



def normalize(text):
    return re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFKD', text.replace('ß', 'ss'))
                  .encode('ascii', 'ignore').decode().lower()).strip()


# Canonical facts are independent of the labels in the challenge set.
SLOTS = {
    'full_name': ('Mara Neumann', 'Full name of the applicant'),
    'first_name': ('Mara', 'First name, given name of the applicant'),
    'last_name': ('Neumann', 'Last name, family name of the applicant'),
    'email': ('mara.neumann@example.com', 'Email address of the applicant'),
    'city': ('Karlsruhe', 'City where the applicant currently lives'),
    'country': ('Germany', 'Country where the applicant currently lives'),
    'company': ('Rheinbogen Systems GmbH', 'Company employer name in work experience'),
    'title': ('Senior Project Manager', 'Job title position in work experience'),
    'work_location': ('Mannheim', 'City location of a job in work experience'),
    'school': ('Karlsruhe Institute of Technology', 'School university institution in education'),
    'degree': ('M.Sc. Industrial Engineering & Management', 'Degree qualification in education'),
}
ALIASES = {
    'full_name': ['name', 'full name', 'vollständiger name'],
    'first_name': ['first name', 'given name', 'vorname'],
    'last_name': ['last name', 'surname', 'nachname'],
    'email': ['email', 'email address', 'e mail adresse'],
    'city': ['city', 'location', 'where do you live', 'wohnort'],
    'country': ['country', 'land'],
    'company': ['company', 'company name', 'employer', 'name', 'unternehmen'],
    'title': ['title', 'job title', 'position', 'role', 'berufsbezeichnung'],
    'work_location': ['location', 'city', 'standort', 'arbeitsort'],
    'school': ['name', 'school', 'institution', 'university', 'hochschule'],
    'degree': ['degree', 'qualification', 'abschluss'],
}


@dataclass(frozen=True)
class Field:
    label: str
    expected: str | None
    section: str = 'Personal details'
    name: str = ''


# Small, hand-authored stress set, not a representative ATS accuracy estimate.
# Each list is one form/section. Repeated fields deliberately test whether
# one-to-one assignment suppresses legitimate repeated answers.
FORMS = {
    'ordinary_contact': [Field('Full name', 'full_name'), Field('Email', 'email'), Field('City', 'city')],
    'paraphrases': [Field('How should we address you?', 'full_name'), Field('Your home town', 'city'), Field('Email address', 'email')],
    'german': [Field('Vorname', 'first_name'), Field('Nachname', 'last_name'), Field('Wohnort', 'city')],
    'opaque_ids': [Field('Your answer', 'first_name', name='candidate[firstName]'), Field('Your answer', 'email', name='candidate[email]')],
    'work_card': [Field('Name', 'company', 'Work experience'), Field('Position', 'title', 'Work experience'), Field('Location', 'work_location', 'Work experience')],
    'education_card': [Field('Name', 'school', 'Education'), Field('Qualification', 'degree', 'Education')],
    'reference_card': [Field('Name', None, 'Reference'), Field('Email', None, 'Reference')],
    'emergency_card': [Field('Name', None, 'Emergency contact'), Field('Email', None, 'Emergency contact')],
    'sensitive': [Field('Gender', None), Field('Nationality', None), Field('Signature', None)],
    'confirmation': [Field('Email', 'email'), Field('Confirm email', 'email')],
    'work_repeated': [Field('Company', 'company', 'Work experience'), Field('Confirm company name', 'company', 'Work experience')],
    'contradiction': [Field('Last name', None, name='firstName')],
    'unrelated': [Field('Favorite city', None), Field('Preferred interview location', None), Field('Name of your pet', None)],
    'international': [Field('City', 'city'), Field('Country', 'country')],
}


def fuzzy_scores(field):
    hint = normalize(field.label)
    return {key: max(SequenceMatcher(None, hint, normalize(alias)).ratio()
                     for alias in aliases) for key, aliases in ALIASES.items()}


def allowed(field):
    section = normalize(field.section)
    if any(word in section for word in ('reference', 'emergency')):
        return set()
    if 'work experience' in section:
        return {'company', 'title', 'work_location'}
    if 'education' in section:
        return {'school', 'degree'}
    return {'full_name', 'first_name', 'last_name', 'email', 'city', 'country'}


def guarded_scores(field, scores):
    # These abstentions are part of this prototype's policy, not learned facts.
    hint = normalize(field.label)
    if any(word in hint.split() for word in ('favorite', 'preferred', 'pet', 'signature', 'nationality', 'gender')):
        return {}
    keys = allowed(field)
    name = normalize(re.sub(r'([a-z])([A-Z])', r'\1 \2', field.name))
    name_matches = {key for key in keys for alias in ALIASES[key]
                    if name.endswith(normalize(alias)) and name}
    exact = {key for key in keys if hint in [normalize(alias) for alias in ALIASES[key]]}
    if exact and name_matches and not exact & name_matches:
        return {}
    result = {key: score for key, score in scores.items() if key in keys}
    if hint == 'your answer' and len(name_matches) == 1:
        result = {next(iter(name_matches)): 1.0}
    # Confirming a fact is distinct from another person having the same label.
    if hint.startswith('confirm '):
        return guarded_scores(Field(field.label[8:], field.expected, field.section), fuzzy_scores(
            Field(field.label[8:], field.expected, field.section)))
    return result


def choose(scores, threshold=.78, margin=.12):
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < threshold:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < margin:
        return None
    return ranked[0][0]


def assignment(rows):
    """Exact maximum-weight assignment with a free abstain option (tiny forms).

    Deliberately tests a one-to-one assumption, not a production-size solver.
    Confidence/margin gates are applied before assignment; optimization cannot
    manufacture confidence by eliminating a competing field.
    """
    keys = list(SLOTS)

    @lru_cache(None)
    def solve(index, occupied):
        if index == len(rows):
            return 0., ()
        score, choices = solve(index + 1, occupied)
        best = score, (None,) + choices
        selected = choose(rows[index])
        if selected is not None:
            bit = 1 << keys.index(selected)
            if not occupied & bit:
                score, choices = solve(index + 1, occupied | bit)
                candidate = score + rows[index][selected], (selected,) + choices
                if candidate[0] > best[0]:
                    best = candidate
        return best

    return list(solve(0, 0)[1])


def baseline(browser, fields):
    page = browser.new_page()
    try:
        html = '<form><h1>Application</h1><input type=file>'
        for i, field in enumerate(fields):
            # Fieldset context is real DOM context, not attached to the input.
            html += f'<fieldset><legend>{escape(field.section)}</legend><label>{escape(field.label)}<input id="q{i}" name="{escape(field.name)}"></label></fieldset>'
        page.set_content(html + '</form>')
        page.add_script_tag(path=str(ROOT / 'extensions/application-companion/history.js'))
        page.add_script_tag(path=str(ROOT / 'extensions/application-companion/best-effort.js'))
        profile = {'first_name': 'Mara', 'last_name': 'Neumann', 'email': SLOTS['email'][0],
                   'city': SLOTS['city'][0], 'country': SLOTS['country'][0],
                   'experiences': [{'id': 'job-1', 'company': SLOTS['company'][0], 'title': SLOTS['title'][0],
                                    'location': SLOTS['work_location'][0], 'start': '2021', 'end': None, 'current': True}],
                   'preferences': {}}
        page.evaluate('(profile) => applicationFields.run(profile)', profile)
        values = page.locator('input:not([type=file])').evaluate_all('(els) => els.map(el => el.value)')
        return [next((key for key, (value, _) in SLOTS.items() if value == actual), None) if actual else None for actual in values]
    finally:
        page.close()


def matching_experiment(embeddings=False):
    from playwright.sync_api import sync_playwright
    predictions = {'current_extension': {}, 'fuzzy_only': {}, 'section_and_margin': {}, 'global_one_to_one': {}}
    semantic_rows = None
    model_seconds = None
    if embeddings:
        from hiring_scraper.app.semantic_search_worker import LocalE5, MODEL_KEY
        started = time.perf_counter()
        model = LocalE5()
        labels = [f'passage: {description}' for _, description in SLOTS.values()]
        fields = [field for fields in FORMS.values() for field in fields]
        vectors = model.encode(labels + [f'query: {field.section}. {field.label}' for field in fields])
        import numpy as np
        similarities = np.asarray(vectors[len(labels):]) @ np.asarray(vectors[:len(labels)]).T
        semantic_rows = iter([dict(zip(SLOTS, row.tolist())) for row in similarities])
        model_seconds = round(time.perf_counter() - started, 3)
        predictions['embedding_only'] = {}
        predictions['embedding_guarded'] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chromium', headless=True)
        try:
            for name, fields in FORMS.items():
                predictions['current_extension'][name] = baseline(browser, fields)
                rows = [fuzzy_scores(field) for field in fields]
                scoped = [guarded_scores(field, row) for field, row in zip(fields, rows)]
                predictions['fuzzy_only'][name] = [choose(row, margin=0) for row in rows]
                predictions['section_and_margin'][name] = [choose(row) for row in scoped]
                predictions['global_one_to_one'][name] = assignment(scoped)
                if semantic_rows is not None:
                    rows = [next(semantic_rows) for _ in fields]
                    # Fixed exploratory thresholds, not calibrated probabilities.
                    predictions['embedding_only'][name] = [choose(row, threshold=.75, margin=0) for row in rows]
                    predictions['embedding_guarded'][name] = [choose(guarded_scores(field, row), threshold=.80, margin=.06)
                                                             for field, row in zip(fields, rows)]
        finally:
            browser.close()
    results = {}
    for technique, forms in predictions.items():
        records = []
        for name, predicted in forms.items():
            for field, actual in zip(FORMS[name], predicted):
                records.append({'form': name, 'section': field.section, 'label': field.label,
                                'expected': field.expected, 'actual': actual,
                                'outcome': 'correct' if actual is not None and actual == field.expected else
                                'wrong' if actual is not None else 'safe_skip' if field.expected is None else 'miss'})
        counts = {key: sum(row['outcome'] == key for row in records) for key in ('correct', 'wrong', 'safe_skip', 'miss')}
        counts['precision'] = round(counts['correct'] / max(1, counts['correct'] + counts['wrong']), 3)
        counts['coverage'] = round(counts['correct'] / sum(row['expected'] is not None for row in records), 3)
        results[technique] = {'counts': counts, 'fields': records}
    return {'field_count': sum(map(len, FORMS.values())), 'techniques': results,
            'embedding_model': MODEL_KEY if embeddings else None, 'embedding_load_and_batch_seconds': model_seconds}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--embeddings', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/autofill-lab/results.json')
    args = parser.parse_args()
    result = {'scope': 'Exploratory synthetic challenge set. Prototype matchers do not write to pages; current_extension runs actual extension source in Chromium.',
              'extension_sha256': hashlib.sha256((ROOT / 'extensions/application-companion/best-effort.js').read_bytes()).hexdigest(),
              'history_sha256': hashlib.sha256((ROOT / 'extensions/application-companion/history.js').read_bytes()).hexdigest(),
              'matching': matching_experiment(args.embeddings)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({name: result['counts'] for name, result in result['matching']['techniques'].items()}, indent=2))
    print(f'Full evidence: {args.output}')


if __name__ == '__main__':
    main()
