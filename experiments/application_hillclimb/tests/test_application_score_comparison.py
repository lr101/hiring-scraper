"""Score acceptance must require every reference and both quality dimensions."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'compare_scores.py'


class ScoreComparisonTests(unittest.TestCase):
    def module(self):
        self.assertTrue(SCRIPT.exists(), 'The baseline comparison command is missing')
        spec = importlib.util.spec_from_file_location('hillclimb_score_comparison', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def compare(self, baseline, candidate):
        return self.module().compare_scores({'documents': baseline}, {'documents': candidate})

    def doc(self, kind, fit, checklist):
        return {'case_id': 'case-002', 'document_kind': kind,
                'job_fit_score': fit, 'checklist_score': checklist}

    def test_equal_scores_pass_when_both_generated_documents_are_present(self):
        result = self.compare([self.doc('cv', 5, 7)],
                              [self.doc('cv', 5, 7), self.doc('cover_letter', 4, 6)])
        self.assertTrue(result['all_score_targets_met'])

    def test_higher_fit_does_not_compensate_for_lower_checklist_score(self):
        result = self.compare([self.doc('cv', 5, 7)],
                              [self.doc('cv', 10, 6), self.doc('cover_letter', 4, 6)])
        self.assertFalse(result['all_score_targets_met'])
        self.assertEqual(result['comparisons'][0]['checklist_delta'], -1)

    def test_missing_unpaired_companion_document_prevents_complete_score_coverage(self):
        result = self.compare([self.doc('cv', 5, 7)], [self.doc('cv', 5, 7)])
        self.assertFalse(result['all_score_targets_met'])
        self.assertEqual(result['missing_generated_documents'], ['case-002-cover_letter'])

    def test_duplicate_scores_cannot_overwrite_a_failed_document(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.compare([self.doc('cv', 5, 7)],
                         [self.doc('cv', 1, 1), self.doc('cv', 10, 10)])

    def test_malformed_rows_and_invalid_score_values_are_rejected(self):
        for row in ['invalid', self.doc('cv', True, 7), self.doc('cv', 11, 7)]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.compare([self.doc('cv', 5, 7)], [row])

    def provenance_fixture(self, root):
        def write(name, value):
            path = root / name
            path.write_text(json.dumps(value))
            return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        baseline = write('baseline.json', {'documents': [self.doc('cv', 5, 7)]})
        candidate = write('scores.json', {'documents': [self.doc('cv', 5, 7), self.doc('cover_letter', 5, 7)]})
        scenario = write('scenario.json', {'version': 1})
        cv = write('base.tex', 'synthetic base source')
        suite = write('suite.json', {'baseline_sha256': baseline['sha256'], 'mode': 'full', 'cases': {
            'case-002': {'scenario_sha256': scenario['sha256'], 'base_cv_sha256': cv['sha256']}}})
        run = write('run.json', {'scenario_id': 'case-002', 'status': 'completed', 'mode': 'full',
                                'requested_model': None, 'model': 'resolved-default',
                                'scenario_sha256': scenario['sha256'], 'base_cv_sha256': cv['sha256'],
                                'code_fingerprint': 'candidate-code'})
        package = write('application.json', {'format': 'radius-application-v1', 'cv': 'CV', 'cover_letter': 'Letter'})
        manifest = {'version': 1, 'suite': suite, 'baseline_sha256': baseline['sha256'],
                    'candidate_scorecard_sha256': candidate['sha256'],
                    'evaluator': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'},
                    'runs': [{'case_id': 'case-002', 'scenario': scenario, 'base_cv': cv,
                              'run': run, 'package': package, 'scored_package_sha256': package['sha256']}]}
        write('provenance.json', manifest)
        return root / 'baseline.json', root / 'scores.json', root / 'provenance.json'

    def test_provenance_accepts_matching_frozen_sources_and_candidate_output(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = self.provenance_fixture(Path(folder))
            self.assertTrue(self.module().verify_provenance(*paths)['verified'])

    def test_changed_scorecard_is_rejected_even_when_scores_still_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = self.provenance_fixture(Path(folder))
            data = json.loads(paths[1].read_text())
            data['documents'][0]['job_fit_score'] = 10
            paths[1].write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'scorecard'):
                self.module().verify_provenance(*paths)

    def test_rehashed_run_from_a_different_scenario_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = self.provenance_fixture(root)
            run = json.loads((root / 'run.json').read_text())
            run['scenario_sha256'] = 'another-scenario'
            (root / 'run.json').write_text(json.dumps(run))
            manifest = json.loads(paths[2].read_text())
            manifest['runs'][0]['run']['sha256'] = hashlib.sha256((root / 'run.json').read_bytes()).hexdigest()
            paths[2].write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'scenario'):
                self.module().verify_provenance(*paths)

    def test_same_sources_with_a_different_requested_model_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = self.provenance_fixture(root)
            run = json.loads((root / 'run.json').read_text())
            run['requested_model'] = 'another-model'
            (root / 'run.json').write_text(json.dumps(run))
            manifest = json.loads(paths[2].read_text())
            manifest['runs'][0]['run']['sha256'] = hashlib.sha256((root / 'run.json').read_bytes()).hexdigest()
            paths[2].write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'model'):
                self.module().verify_provenance(*paths)

    def test_legacy_run_without_requested_model_cannot_prove_default_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = self.provenance_fixture(root)
            run = json.loads((root / 'run.json').read_text())
            del run['requested_model']
            (root / 'run.json').write_text(json.dumps(run))
            manifest = json.loads(paths[2].read_text())
            manifest['runs'][0]['run']['sha256'] = hashlib.sha256((root / 'run.json').read_bytes()).hexdigest()
            paths[2].write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'model'):
                self.module().verify_provenance(*paths)


if __name__ == '__main__':
    unittest.main()
