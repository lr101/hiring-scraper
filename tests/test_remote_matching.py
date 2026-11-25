"""GPT-6 Luna or the local models, switched by hand, with no automatic fallback."""
import json
import os
import tempfile
import unittest
from unittest import mock

from hiring_scraper.app import job_analysis_worker as worker
from hiring_scraper.app import remote_matching as luna
from hiring_scraper.matching_v3 import weights as W


from fastapi import HTTPException


class ModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'RADIUS_CODEX_STATE_DIR': self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_mode_defaults_to_luna_and_persists(self):
        self.assertEqual(luna.mode(), 'luna')
        luna.set_mode('local')
        self.assertEqual(luna.mode(), 'local')
        with self.assertRaises(ValueError):
            luna.set_mode('cloud')

    def test_earlier_luna_first_setting_reads_as_luna(self):
        luna._settings_path().write_text(json.dumps({'mode': 'luna_first'}))
        self.assertEqual(luna.mode(), 'luna')

    def test_status_reports_whether_the_selected_model_can_run(self):
        with mock.patch.object(luna.codex_runtime, 'status', return_value={'enabled': True}):
            self.assertEqual(luna.status(), {'mode': 'luna', 'ready': True, 'reason': ''})
        with mock.patch.object(luna.codex_runtime, 'status', return_value={'enabled': False}):
            self.assertFalse(luna.status()['ready'])
        luna.set_mode('local')
        self.assertEqual(luna.status(), {'mode': 'local', 'ready': True, 'reason': ''})

    def test_service_errors_keep_work_queued_but_too_long_postings_fail(self):
        for status, detail in ((429, 'ChatGPT rate limit reached.'), (401, 'Continue with ChatGPT'),
                               (504, 'ChatGPT preparation timed out.')):
            with self.subTest(status=status), \
                    mock.patch.object(luna.codex_runtime, 'generate', side_effect=HTTPException(status, detail)):
                with self.assertRaises(luna.LunaUnavailable):
                    luna.analyse_lines(['Job title: PO'])
        too_long = HTTPException(502, 'The application exceeds the model context limit. Reduce the CV.')
        with mock.patch.object(luna.codex_runtime, 'generate', side_effect=too_long):
            with self.assertRaises(ValueError) as raised:
                luna.analyse_lines(['Job title: PO'])
        self.assertNotIsInstance(raised.exception, OSError)


ANALYSIS = {'occupation': 'Product management', 'industry': 'Software',
            'duties': [{'text': 'Run discovery interviews'}],
            'requirements': [{'text': 'Erfahrung in der Koordination von Teams', 'need': 'must', 'kind': 'experience'},
                             {'text': 'Strukturierte Arbeitsweise', 'need': 'must', 'kind': 'skill'}]}
SOURCE = {'units': [{'source': 'skill_evidence.Figma', 'text': 'Figma: prototypes'},
                    {'source': 'cv.experiences[0]', 'text': 'Coordinated a company-wide initiative with four departments.'}],
          'roles': ['Product Owner']}


class EvidenceMappingTests(unittest.TestCase):
    def test_reply_maps_onto_reranker_evidence_shape(self):
        reply = {'items': [{'ref': 'd0', 'line': 0, 'support': 0.4}, {'ref': 'r0', 'line': 1, 'support': 1.7},
                           {'ref': 'zz', 'line': 1, 'support': 0.9}],
                 'role': {'index': 0, 'support': 0.8}, 'domain': {'line': 1, 'support': 0.6}}
        evidence = luna.evidence_from_reply(reply, ANALYSIS, 'Product Owner', SOURCE)
        self.assertEqual(evidence['provider'], 'luna')
        # Personal traits are not scored, so only the duty and the experience claim appear.
        self.assertEqual([row['ref'] for row in evidence['items']], ['d0', 'r0'])
        self.assertEqual(evidence['items'][1]['support'], 1.0)
        self.assertEqual(evidence['items'][1]['source'], 'cv.experiences[0]')
        self.assertEqual(evidence['role'], {'support': 0.8, 'role': 'Product Owner'})
        self.assertEqual(evidence['domain']['field'], 'Product management; Software')

    def test_out_of_range_lines_give_no_support(self):
        reply = {'items': [{'ref': 'd0', 'line': 9, 'support': 0.9}], 'role': {'index': 5, 'support': 0.2},
                 'domain': {'line': -1, 'support': 0.1}}
        evidence = luna.evidence_from_reply(reply, ANALYSIS, 'Product Owner', SOURCE)
        self.assertEqual(evidence['items'][0]['support'], 0.0)
        self.assertEqual(evidence['items'][1]['support'], 0.0)  # r0 missing from the reply

    def test_prompt_lists_items_cv_lines_and_roles(self):
        prompt = luna.evidence_prompt(ANALYSIS, 'Product Owner', SOURCE)
        self.assertIn('r0 [requirement] Erfahrung in der Koordination von Teams', prompt)
        self.assertNotIn('Strukturierte Arbeitsweise', prompt)
        self.assertIn('1 Coordinated a company-wide initiative', prompt)
        self.assertIn('0 Product Owner', prompt)


class WorkerModelSelectionTests(unittest.TestCase):
    LINES = ['Job title: Product Owner', 'Ihre Aufgaben', 'Run discovery interviews']
    SCOPES = ['title', 'heading', 'duty']
    LUNA_OUTPUT = 'OCC Product\nIND Software\nDUTIES\nD 2 | Run discovery interviews\nREQUIREMENTS\nEND'

    def test_luna_mode_never_calls_the_local_models(self):
        with mock.patch.object(luna, 'use_luna', return_value=True), \
                mock.patch.object(luna, 'analyse_lines', return_value=self.LUNA_OUTPUT), \
                mock.patch.object(worker, 'analyse_lines') as local_llm:
            payload = worker._analyse(self.LINES, self.SCOPES, worker.analyse_lines)
        local_llm.assert_not_called()
        self.assertEqual(payload['provider'], 'luna')
        self.assertEqual([row['text'] for row in payload['duties']], ['Run discovery interviews'])
        with mock.patch.object(luna, 'use_luna', return_value=True), \
                mock.patch.object(luna, 'compute_evidence', return_value={'provider': 'luna'}), \
                mock.patch.object(worker, 'compute_evidence') as local_reranker:
            self.assertEqual(worker._evidence(ANALYSIS, 'PO', SOURCE, b'', worker.embed, worker.rerank),
                             {'provider': 'luna'})
        local_reranker.assert_not_called()

    def test_unreachable_luna_raises_os_error_instead_of_falling_back(self):
        # OSError pauses the stage and keeps the job queued (see _Stage.run).
        with mock.patch.object(luna, 'use_luna', return_value=True), \
                mock.patch.object(luna, 'analyse_lines', side_effect=luna.LunaUnavailable('rate limited')), \
                mock.patch.object(worker, 'analyse_lines') as local_llm:
            with self.assertRaises(OSError):
                worker._analyse(self.LINES, self.SCOPES, worker.analyse_lines)
        local_llm.assert_not_called()
        with mock.patch.object(luna, 'use_luna', return_value=True), \
                mock.patch.object(luna, 'compute_evidence', side_effect=luna.LunaUnavailable('busy')), \
                mock.patch.object(worker, 'compute_evidence') as local_reranker:
            with self.assertRaises(OSError):
                worker._evidence(ANALYSIS, 'PO', SOURCE, b'', worker.embed, worker.rerank)
        local_reranker.assert_not_called()

    def test_local_mode_and_injected_models_never_call_luna(self):
        local = {'items': [], 'role': None, 'domain': None, 'context': 'x'}
        with mock.patch.object(luna, 'use_luna', return_value=False), \
                mock.patch.object(luna, 'compute_evidence') as remote, \
                mock.patch.object(worker, 'compute_evidence', return_value=local):
            self.assertIs(worker._evidence(ANALYSIS, 'PO', SOURCE, b'', worker.embed, worker.rerank), local)
        remote.assert_not_called()
        with mock.patch.object(luna, 'use_luna', return_value=True) as selected, \
                mock.patch.object(worker, 'compute_evidence', return_value=local):
            worker._evidence(ANALYSIS, 'PO', SOURCE, b'', worker.embed, lambda q, d: [0.0] * len(d))
            selected.assert_not_called()


class CalibrationTests(unittest.TestCase):
    def test_score_uses_the_calibration_of_the_evidence_provider(self):
        from hiring_scraper.matching_v3.score import calibrate
        self.assertNotEqual(W.LUNA_CALIBRATION_KNOTS, W.CALIBRATION_KNOTS)
        self.assertNotEqual(calibrate(60.0, W.LUNA_CALIBRATION_KNOTS), calibrate(60.0, W.CALIBRATION_KNOTS))


class LunaEvaluationTests(unittest.TestCase):
    """Recorded Luna outputs on the public set stay within the local baseline's gates."""

    def test_luna_meets_the_local_regression_gates(self):
        from tests.eval import run
        from tests.test_eval_regression import MIN_PAIR_COUNT, check_regression_gates
        with mock.patch.dict(os.environ, {'RADIUS_EVAL_PROVIDER': 'luna'}):
            report = run.evaluate('v3', dataset='public', split='all')
        self.assertEqual(report['overall']['count'], 71)
        baseline = json.loads(run.baseline_path('public').read_text(encoding='utf-8'))['v3']
        failures = check_regression_gates(report, baseline, min_pairs=MIN_PAIR_COUNT['public'])
        self.assertFalse(failures, failures)


if __name__ == '__main__':
    unittest.main()
