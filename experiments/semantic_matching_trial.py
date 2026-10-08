"""Frozen, optional multilingual retrieval trial; never used by production matching.

Requires an isolated environment containing sentence-transformers and CPU torch.
Full source jobs remain local. Predictions are saved before reading any labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hiring_scraper import matching
from hiring_scraper.requirements import scoped_sentences, structured_text
from experiments.cv_profile_evaluation import expanded_query, lexical_scores, metrics

MODELS = {
    'intfloat/multilingual-e5-small': '614241f622f53c4eeff9890bdc4f31cfecc418b3',
    'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1': '1427fd652930e4ba29e8149678df786c240d8825',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def predict(jobs, profile):
    import torch
    from sentence_transformers import CrossEncoder, SentenceTransformer

    torch.set_num_threads(2)
    query = 'Roles: ' + ', '.join(profile['desired_roles'])
    query += '. Adjacent roles: ' + ', '.join(profile['secondary_roles'])
    query += '. Professional experience: ' + profile['summary']
    query += ' Professional skills: ' + ', '.join(
        skill for skill, evidence in profile.get('skill_evidence', {}).items()
        if evidence.get('context') == 'professional')
    scoped = [' '.join([job['title'], *[
        sentence for sentence, scope in scoped_sentences(structured_text(job.get('description')))
        if scope != 'benefits']]) for job in jobs]
    chunks, owners = [], []
    for index, text in enumerate(scoped):
        words = text.split()
        for start in range(0, max(1, len(words)), 300):
            chunks.append('passage: ' + jobs[index]['title'] + ' ' + ' '.join(words[start:start + 300]))
            owners.append(index)
    started = time.monotonic()
    encoder_name, reranker_name = MODELS
    encoder = SentenceTransformer(encoder_name, revision=MODELS[encoder_name],
                                  device='cpu', trust_remote_code=False)
    embeddings = encoder.encode(chunks, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
    query_embedding = encoder.encode(['query: ' + query], normalize_embeddings=True, show_progress_bar=False)[0]
    dense_scores = [-1.0] * len(jobs)
    for owner, score in zip(owners, embeddings @ query_embedding):
        dense_scores[owner] = max(dense_scores[owner], float(score))
    dense_seconds = time.monotonic() - started
    rules = [matching.match_job(job, profile) for job in jobs]
    lexical = lexical_scores([{**job, 'description': scoped[index]} for index, job in enumerate(jobs)],
                             expanded_query(profile), title_weight=3)

    def ordered(values):
        return sorted(range(len(jobs)), key=lambda index: (-values[index], jobs[index]['id']))

    baseline = [index for index in ordered([row['score'] for row in rules])
                if rules[index]['eligible'] and rules[index]['score'] >= 20]
    dense = [index for index in ordered(dense_scores) if rules[index]['eligible']][:30]
    lex = [index for index in ordered(lexical) if rules[index]['eligible'] and lexical[index] > 0][:30]
    union = sorted(set(baseline + dense + lex))
    del encoder, embeddings, query_embedding
    started = time.monotonic()
    reranker = CrossEncoder(reranker_name, revision=MODELS[reranker_name], device='cpu',
                           max_length=384, trust_remote_code=False)
    values = reranker.predict([(query, scoped[index]) for index in union],
                             batch_size=8, show_progress_bar=False) if union else []
    rerank_scores = {index: float(value) for index, value in zip(union, values)}
    reranked = sorted(union, key=lambda index: (-rerank_scores[index], jobs[index]['id']))
    return {
        'model_revisions': MODELS, 'rule_version': matching.VERSION,
        'dense_seconds': round(dense_seconds, 3),
        'rerank_seconds': round(time.monotonic() - started, 3), 'query': query,
        'rankings': {name: [jobs[index]['id'] for index in indexes] for name, indexes in
                     [('rules', baseline), ('lexical_top30', lex), ('dense_top30', dense),
                      ('union_reranked', reranked)]},
        'scores': {job['id']: {'rule': rules[index]['score'], 'eligible': rules[index]['eligible'],
                              'dense': dense_scores[index], 'lexical': lexical[index],
                              'rerank': rerank_scores.get(index)} for index, job in enumerate(jobs)},
    }


def evaluate_frozen(predictions, jobs, label_rows):
    by_external_id = {job['external_id']: job for job in jobs}
    labels = {}
    for label in label_rows:
        job = by_external_id[label['external_id']]
        expected = hashlib.sha256((job['title'] + '\n' + job['description']).encode()).hexdigest()
        if label['document_sha256'] != expected or job['id'] in labels:
            raise ValueError('Duplicate judgment or changed source text')
        labels[job['id']] = label
    if set(labels) != {job['id'] for job in jobs}:
        raise ValueError('Every trial job needs exactly one frozen judgment')
    return {name: metrics([{'id': key} for key in ids], labels)
            for name, ids in predictions['rankings'].items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('jobs', 'profile', 'output'):
        parser.add_argument('--' + field, required=True, type=Path)
    parser.add_argument('--labels', type=Path)
    args = parser.parse_args()
    jobs = json.loads(args.jobs.read_text())
    if not jobs or len({job['id'] for job in jobs}) != len(jobs):
        raise ValueError('Trial requires a nonempty pool with unique job IDs')
    result = predict(jobs, json.loads(args.profile.read_text()))
    result.update(input_sha256=digest(args.jobs), profile_sha256=digest(args.profile))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    if args.labels:
        measured = evaluate_frozen(result, jobs, json.loads(args.labels.read_text()))
        summary = {'predictions_sha256': digest(args.output), 'labels_sha256': digest(args.labels),
                   'algorithms': measured}
        args.output.with_suffix('.metrics.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(measured, indent=2))


if __name__ == '__main__':
    main()
