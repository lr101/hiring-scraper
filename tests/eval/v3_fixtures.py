"""Record local-model outputs for the v3 matcher evaluation.

CI cannot run the LLM or reranker, so this script stores their outputs per
dataset: job analyses (LLM, per job) and requirement evidence (E5 + reranker, per
labelled case). ``tests.eval.run --matcher v3`` then scores from these files.

Usage (needs the local model servers, see docs/local-models.md):
    python -m tests.eval.v3_fixtures [--dataset public|private] [--stage analyses|evidence|all]

Entries whose source hash and version are unchanged are reused, so reruns are
incremental and resumable.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from hiring_scraper.job_analysis import (
    VERSION as ANALYSIS_VERSION, analysis_payload, job_line_entries, result_hash, source_hash,
)
from hiring_scraper.matching_v3.evidence import (
    VERSION as EVIDENCE_VERSION,
    compute_evidence,
    embed_units,
    evidence_hash,
    units_hash,
    units_source,
)

EVAL_DIR = Path(__file__).resolve().parent


def fixture_dir(dataset: str) -> Path:
    if dataset == 'public':
        return EVAL_DIR / 'public' / 'v3'
    from tests.eval import cv as private_dataset
    return private_dataset.CV_EVAL_DIR / "v3"


def load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(path)


def _embedder():
    from hiring_scraper.app.semantic_search_worker import LocalE5
    model = LocalE5()

    def embed(texts: list[str], kind: str) -> list[list[float]]:
        return model.encode([f'{kind}: {text}' for text in texts])
    return embed


def run_analyses(dataset_module, out: Path) -> None:
    from hiring_scraper.app.local_llm import analyse_lines
    path = out / 'analyses.json'
    data = load_fixture(path)
    jobs = dataset_module.load_jobs()
    for number, (job_id, job) in enumerate(sorted(jobs.items()), 1):
        entries = job_line_entries(job)
        lines, scopes = [line for line, _ in entries], [scope for _, scope in entries]
        digest = source_hash(lines)
        row = data.get(job_id)
        if row and row.get('version') == ANALYSIS_VERSION and row.get('source_hash') == digest:
            continue
        started = time.monotonic()
        payload = analysis_payload(lines, analyse_lines(lines), scopes)
        data[job_id] = {'version': ANALYSIS_VERSION, 'source_hash': digest,
                        'result_hash': result_hash(ANALYSIS_VERSION, payload), 'payload': payload}
        _save(path, data)
        print(f'[{number}/{len(jobs)}] {job_id} {time.monotonic() - started:.0f}s '
              f'{len(payload["duties"])}D {len(payload["requirements"])}R', flush=True)


def run_evidence(dataset_module, out: Path) -> None:
    from hiring_scraper.app.local_llm import rerank
    analyses = load_fixture(out / 'analyses.json')
    path = out / 'evidence.json'
    data = load_fixture(path)
    embed = _embedder()
    unit_cache: dict[str, tuple[dict, str, bytes]] = {}
    cases = dataset_module.load_cases(split='all')
    for number, case in enumerate(cases, 1):
        analysis = analyses.get(case['job_id'])
        if not analysis or analysis.get('version') != ANALYSIS_VERSION:
            print(f'skip {case["id"]}: no current analysis', file=sys.stderr)
            continue
        if case['profile_id'] not in unit_cache:
            source = units_source(case['profile'])
            unit_cache[case['profile_id']] = (source, units_hash(source), embed_units(source, embed))
        source, profile_hash, vectors = unit_cache[case['profile_id']]
        digest = evidence_hash(analysis['result_hash'], profile_hash)
        row = data.get(case['id'])
        if row and row.get('version') == EVIDENCE_VERSION and row.get('hash') == digest:
            continue
        started = time.monotonic()
        payload = compute_evidence(analysis['payload'], str(case['job'].get('title') or ''), source, vectors,
                                   embed=embed, rerank=rerank)
        data[case['id']] = {'version': EVIDENCE_VERSION, 'hash': digest, 'payload': payload}
        if number % 5 == 0:
            _save(path, data)
        print(f'[{number}/{len(cases)}] {case["id"]} {time.monotonic() - started:.1f}s', flush=True)
    _save(path, data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=('public', 'private'), default='public')
    parser.add_argument('--stage', choices=('analyses', 'evidence', 'all'), default='all')
    args = parser.parse_args(argv)
    from tests.eval.run import get_dataset
    dataset_module = get_dataset(args.dataset)
    out = fixture_dir(args.dataset)
    if args.stage in ('analyses', 'all'):
        run_analyses(dataset_module, out)
    if args.stage in ('evidence', 'all'):
        run_evidence(dataset_module, out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
