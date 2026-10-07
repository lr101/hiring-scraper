"""Repeatable offline comparison against independent profile/job judgments.

Full employer responses remain in local capture files. Example:
python experiments/cv_profile_evaluation.py --jobs data/local-cv-board/evaluation/regional-heldout-input.json --labels experiments/fixtures/cv_job_labels/regional-heldout.json --profile fixtures/cv_profile.json --output /tmp/heldout-results.json
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hiring_scraper import matching

GRADES = {'unlikely': 0, 'possible': 1, 'recommended': 2}
STOP = {'and', 'und', 'the', 'with', 'for', 'von', 'der', 'die', 'das', 'den', 'des',
        'ein', 'eine', 'einer', 'eines', 'your', 'our', 'are', 'you', 'wir', 'sie', 'mit'}


def tokenize(text):
    return [word for word in re.findall(r'[a-zäöüß][a-zäöüß0-9+#.-]{2,}', text.casefold()) if word not in STOP]


def lexical_scores(jobs, query, title_weight=1):
    documents = [Counter(tokenize((str(job.get('title') or '') + ' ') * title_weight +
                                 matching.plain_text(job.get('description')))) for job in jobs]
    size = len(documents)
    lengths = [sum(doc.values()) for doc in documents]
    average = sum(lengths) / max(1, size)
    frequency = Counter(word for doc in documents for word in doc)
    words = set(tokenize(query))
    scores = []
    for document, length in zip(documents, lengths):
        value = 0.0
        for word in words & document.keys():
            count = document[word]
            idf = math.log(1 + (size - frequency[word] + .5) / (frequency[word] + .5))
            value += idf * count * 2.2 / (count + 1.2 * (.25 + .75 * length / max(1, average)))
        scores.append(value)
    return scores


def metrics(ranking, labels):
    """Measure the returned list; missing relevant records contribute zero gain."""
    relevant = {key for key, label in labels.items() if label['fit'] != 'unlikely'}
    hits = sum(row['id'] in relevant for row in ranking)
    ideal = sorted((GRADES[label['fit']] for label in labels.values()), reverse=True)
    def dcg(values):
        return sum((2 ** value - 1) / math.log2(index + 2) for index, value in enumerate(values))
    result = {'judged_records': len(labels), 'relevant_records': len(relevant), 'returned': len(ranking),
              'pooled_precision': hits / len(ranking) if ranking else None,
              'pooled_recall': hits / len(relevant) if relevant else None}
    for limit in (10, 20):
        top = ranking[:limit]
        denominator = dcg(ideal[:limit])
        result[f'ndcg_{limit}'] = dcg([GRADES[labels[row['id']]['fit']] for row in top]) / denominator if denominator else None
        result[f'precision_{limit}'] = sum(row['id'] in relevant for row in top) / len(top) if top else None
        result[f'returned_{limit}'] = len(top)
    return {key: round(value, 4) if isinstance(value, float) else value for key, value in result.items()}


def load_original(revision):
    # This runs trusted code from this repository's recorded baseline, never a remote response.
    code = subprocess.check_output(['git', 'show', f'{revision}:hiring_scraper/matching.py'], text=True)
    module = types.ModuleType('recorded_original_matching')
    exec(compile(code, 'recorded_original_matching.py', 'exec'), module.__dict__)
    return module


def expanded_query(profile):
    terms = list(profile.get('skills', [])) + list(profile.get('desired_roles', [])) + list(profile.get('secondary_roles', []))
    skills = matching.normalize_skills(profile.get('skills', []))
    for skill in skills:
        terms.extend(matching.SKILLS.get(skill, []))
    roles = profile.get('desired_roles', []) + profile.get('secondary_roles', [])
    for group in matching.ROLE_GROUPS:
        if any(matching._role_match(role, alias) for role in roles for alias in group):
            terms.extend(group)
    return ' '.join(terms)


def evaluate(jobs, label_rows, profile, *, threshold=20, baseline_revision='c89a50f0'):
    labels = {str(row['id']): row for row in label_rows}
    if len(labels) != len(label_rows) or any(row.get('fit') not in GRADES for row in label_rows):
        raise ValueError('Labels need unique IDs and a recognized fit class')
    job_by_id = {str(job['id']): job for job in jobs}
    if len(job_by_id) != len(jobs):
        raise ValueError('Job IDs must be unique')
    missing = set(labels) - job_by_id.keys()
    if missing:
        raise ValueError(f'Missing jobs for {len(missing)} labels')
    for key, label in labels.items():
        job = job_by_id[key]
        digest = hashlib.sha256((str(job.get('title') or '') + '\n' + str(job.get('description') or '')).encode()).hexdigest()
        if label.get('document_sha256') and label['document_sha256'] != digest:
            raise ValueError(f'Job text changed after judgment: {key}')
    pool = [{**job_by_id[key], 'id': key} for key in labels]
    original = load_original(baseline_revision)
    literal_terms = profile.get('skills', []) + profile.get('desired_roles', [])
    query = ' '.join(literal_terms + profile.get('secondary_roles', []))
    bm25 = lexical_scores(pool, query)
    expanded = lexical_scores(pool, expanded_query(profile), title_weight=3)
    rankings = {name: [] for name in ('original_rules', 'current_rules', 'literal_keywords', 'bm25', 'alias_bm25')}
    explanations = {}
    for job, lexical, alias_lexical in zip(pool, bm25, expanded):
        key = job['id']
        before = original.match_job(job, profile)
        after = matching.match_job(job, profile)
        explanations[key] = after
        common = {'id': key, 'title': job['title'], 'fit': labels[key]['fit']}
        for name, match in [('original_rules', before), ('current_rules', after)]:
            if match['eligible'] and match['score'] >= threshold:
                rankings[name].append({**common, 'score': match['score']})
        text = str(job.get('title') or '') + ' ' + matching.plain_text(job.get('description'))
        count = sum(bool(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text, re.I)) for term in literal_terms)
        for name, score in [('literal_keywords', count), ('bm25', lexical), ('alias_bm25', alias_lexical)]:
            if score > 0:
                rankings[name].append({**common, 'score': score})
    for rows in rankings.values():
        rows.sort(key=lambda row: (-row['score'], row['id']))
    # Fusion changes ordering only; profile constraints and inclusion stay authoritative.
    lexical_ranks = {row['id']: index + 1 for index, row in enumerate(rankings['alias_bm25'])}
    for weight in (.25, .5, 1):
        fused = [{**row, 'score': 1 / (60 + index + 1) + weight / (60 + lexical_ranks.get(row['id'], len(pool) + 1))}
                 for index, row in enumerate(rankings['current_rules'])]
        fused.sort(key=lambda row: (-row['score'], row['id']))
        rankings[f'constraint_fusion_{weight:g}'] = fused
    source = Path(matching.__file__)
    fingerprint = hashlib.sha256(source.read_bytes())
    requirements_module = source.with_name('requirements.py')
    if requirements_module.exists():
        fingerprint.update(requirements_module.read_bytes())
    return {'baseline_revision': baseline_revision, 'rule_version': matching.VERSION,
            'matching_sha256': fingerprint.hexdigest(), 'threshold': threshold,
            'notes': ['Judgments are cheaper-model assessments, not human hiring outcomes.',
                      'Precision and recall apply only to this pooled judgment set; market recall is unknown.',
                      'Ranking metrics use returned candidates after constraints; missing candidates have zero gain.',
                      'Lexical baselines select any positive lexical match; scores are not comparable to 0–100 rules.'],
            'algorithms': {name: {'metrics': metrics(rows, labels), 'top20': rows[:20]} for name, rows in rankings.items()},
            'current_matches': explanations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('jobs', 'labels', 'profile', 'output'):
        parser.add_argument('--' + field, required=True, type=Path)
    parser.add_argument('--threshold', type=int, choices=range(101), default=20)
    parser.add_argument('--baseline-revision', default='c89a50f0')
    args = parser.parse_args()
    result = evaluate(json.loads(args.jobs.read_text()), json.loads(args.labels.read_text()),
                      json.loads(args.profile.read_text()), threshold=args.threshold,
                      baseline_revision=args.baseline_revision)
    result['input_sha256'] = {name: hashlib.sha256(getattr(args, name).read_bytes()).hexdigest()
                              for name in ('jobs', 'labels', 'profile')}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({name: value['metrics'] for name, value in result['algorithms'].items()}, indent=2))


if __name__ == '__main__':
    main()
