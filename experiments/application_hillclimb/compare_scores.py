#!/usr/bin/env python3
"""Compare independently assigned document scores against a frozen baseline."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def _index(payload):
    rows = payload.get('documents')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Expected a nonempty documents list.')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Each document score must be an object.')
        case_id, kind = row.get('case_id'), row.get('document_kind')
        if not isinstance(case_id, str) or not case_id or kind not in ('cv', 'cover_letter'):
            raise ValueError('Each score needs a case_id and cv/cover_letter document_kind.')
        key = f'{case_id}-{kind}'
        if key in result:
            raise ValueError(f'Duplicate document score: {key}')
        for field in ('job_fit_score', 'checklist_score'):
            if type(row.get(field)) is not int or not 1 <= row[field] <= 10:
                raise ValueError(f'{key}: {field} must be an integer from 1 to 10.')
        result[key] = row
    return result


def compare_scores(baseline, candidate):
    targets, scores = _index(baseline), _index(candidate)
    cases = {row['case_id'] for row in targets.values()}
    expected = {f'{case_id}-{kind}' for case_id in cases for kind in ('cv', 'cover_letter')}
    missing = sorted(expected - scores.keys())
    comparisons = []
    for key, target in targets.items():
        actual = scores.get(key)
        fit_delta = actual['job_fit_score'] - target['job_fit_score'] if actual else None
        checklist_delta = actual['checklist_score'] - target['checklist_score'] if actual else None
        comparisons.append({
            'document_id': key,
            'baseline_job_fit': target['job_fit_score'],
            'baseline_checklist': target['checklist_score'],
            'candidate_job_fit': actual['job_fit_score'] if actual else None,
            'candidate_checklist': actual['checklist_score'] if actual else None,
            'job_fit_delta': fit_delta,
            'checklist_delta': checklist_delta,
            'passed': actual is not None and fit_delta >= 0 and checklist_delta >= 0,
        })
    return {
        'all_score_targets_met': not missing and all(row['passed'] for row in comparisons),
        'baseline_document_count': len(targets),
        'expected_generated_document_count': len(expected),
        'missing_generated_documents': missing,
        'comparisons': comparisons,
        'scope': 'Score coverage and thresholds only. Generation, grounding, source integrity and rendered artifacts require their own evidence.',
    }


def verify_provenance(baseline_path, candidate_path, manifest_path):
    """Bind assigned scores to a trusted frozen suite and actual generated files.

    The caller must supply the independently archived suite and evaluator's
    scored package digests. Hash equality establishes identity, not score merit.
    """
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('version') != 1:
        raise ValueError('Expected a version 1 provenance manifest.')

    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def checked_file(record, label):
        if not isinstance(record, dict) or not isinstance(record.get('path'), str):
            raise ValueError(f'Missing {label} file reference.')
        path = manifest_path.parent / record['path']
        if digest(path) != record.get('sha256'):
            raise ValueError(f'{label} file hash mismatch.')
        return path

    baseline_hash, candidate_hash = digest(baseline_path), digest(candidate_path)
    if manifest.get('baseline_sha256') != baseline_hash:
        raise ValueError('Frozen baseline hash mismatch.')
    if manifest.get('candidate_scorecard_sha256') != candidate_hash:
        raise ValueError('Candidate scorecard hash mismatch.')
    suite_path = checked_file(manifest.get('suite'), 'suite')
    suite = json.loads(suite_path.read_text())
    targets = _index(json.loads(Path(baseline_path).read_text()))
    cases = {row['case_id'] for row in targets.values()}
    if suite.get('baseline_sha256') != baseline_hash or set(suite.get('cases', {})) != cases:
        raise ValueError('Suite baseline or case coverage mismatch.')
    if suite.get('mode') != 'full':
        raise ValueError('The baseline suite requires full generation mode.')
    evaluator = manifest.get('evaluator', {})
    if evaluator.get('model') != 'gpt-6.1-sol' or evaluator.get('reasoning_effort') != 'high':
        raise ValueError('Expected the requested GPT-6.1 Sol high evaluator provenance.')
    runs = manifest.get('runs')
    if not isinstance(runs, list) or not all(isinstance(row, dict) for row in runs):
        raise ValueError('Expected per-case run provenance records.')
    seen, fingerprints, verified = set(), set(), []
    for record in runs:
        case_id = record.get('case_id')
        if case_id not in cases or case_id in seen:
            raise ValueError('Unknown or duplicate candidate run case.')
        seen.add(case_id)
        expected = suite['cases'][case_id]
        scenario = checked_file(record.get('scenario'), 'scenario')
        cv = checked_file(record.get('base_cv'), 'base CV')
        run_path = checked_file(record.get('run'), 'run metadata')
        package_path = checked_file(record.get('package'), 'generated package')
        run = json.loads(run_path.read_text())
        if run.get('status') != 'completed' or run.get('scenario_id') != case_id or run.get('mode') != 'full':
            raise ValueError('Candidate run is incomplete or has the wrong case/mode.')
        if 'requested_model' not in run or run['requested_model'] != suite.get('requested_model'):
            raise ValueError('Candidate requested model differs from the frozen suite.')
        for field, path in [('scenario_sha256', scenario), ('base_cv_sha256', cv)]:
            if digest(path) != expected.get(field) or run.get(field) != expected.get(field):
                raise ValueError(f'Candidate {case_id} {field} differs from the frozen suite.')
        package_hash = digest(package_path)
        if record.get('scored_package_sha256') != package_hash:
            raise ValueError('Scored package digest differs from the generated package.')
        package = json.loads(package_path.read_text())
        if (package.get('format') != 'radius-application-v1' or
                not all(isinstance(package.get(key), str) and package[key].strip() for key in ('cv', 'cover_letter'))):
            raise ValueError('Candidate package lacks complete application documents.')
        fingerprint = run.get('code_fingerprint')
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError('Missing candidate code fingerprint.')
        fingerprints.add(fingerprint)
        verified.append({'case_id': case_id, 'package_sha256': package_hash, 'run_sha256': digest(run_path)})
    if seen != cases or len(fingerprints) != 1:
        raise ValueError('Candidate run coverage or code fingerprint mismatch.')
    return {'verified': True, 'suite_sha256': digest(suite_path), 'baseline_sha256': baseline_hash,
            'candidate_scorecard_sha256': candidate_hash, 'code_fingerprint': next(iter(fingerprints)),
            'evaluator': evaluator, 'runs': verified}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--provenance', required=True, help='Manifest binding scores to the archived suite and candidate files.')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    result = compare_scores(json.loads(Path(args.baseline).read_text()),
                            json.loads(Path(args.candidate).read_text()))
    result['provenance'] = verify_provenance(args.baseline, args.candidate, args.provenance)
    output = Path(args.output)
    # Output may contain private case identifiers; create without world access.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(f'Score targets met: {result["all_score_targets_met"]}; comparison: {output}')
    return 0 if result['all_score_targets_met'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
