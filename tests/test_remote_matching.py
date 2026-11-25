"""GPT-6 Luna as the primary matching model with the local models as backup."""
import json
import os
import tempfile
import unittest
from unittest import mock

from hiring_scraper.app import job_analysis_worker as worker
from hiring_scraper.app import remote_matching as luna
from hiring_scraper.matching_v3 import weights as W


def usage(five_hour=10, weekly=20, available=True):
    return {'available': available, 'used_percent': five_hour, 'long_used_percent': weekly}


class ModeAndHeadroomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'RADIUS_CODEX_STATE_DIR': self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_mode_defaults_to_luna_first_and_persists(self):
        self.assertEqual(luna.mode(), 'luna_first')
        luna.set_mode('local')
        self.assertEqual(luna.mode(), 'local')
        with self.assertRaises(ValueError):
            luna.set_mode('cloud')

    def test_headroom_leaves_the_subscription_for_applications(self):
        self.assertEqual(luna.headroom(usage()), (True, 'ready'))
        self.assertFalse(luna.headroom(usage(five_hour=60))[0])
        self.assertFalse(luna.headroom(usage(weekly=70))[0])
        self.assertFalse(luna.headroom(usage(available=False))[0])
        self.assertFalse(luna.headroom({})[0])
        with mock.patch.dict(os.environ, {'RADIUS_MATCH_LUNA_MAX_WEEKLY': '90'}):
            self.assertTrue(luna.headroom(usage(weekly=80))[0])

    def test_local_mode_never_uses_luna(self):
        luna.set_mode('local')
        with mock.patch.object(luna, '_usage', return_value=usage()):
            self.assertFalse(luna.luna_allowed())
            self.assertEqual(luna.status()['active'], 'local')


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


class WorkerFallbackTests(unittest.TestCase):
    LINES = ['Job title: Product Owner', 'Ihre Aufgaben', 'Run discovery interviews']
    SCOPES = ['title', 'heading', 'duty']

    def test_luna_analysis_is_marked_and_failures_fall_back(self):
        with mock.patch.object(luna, 'luna_allowed', return_value=True), \
                mock.patch.object(luna, 'analyse_lines', return_value='OCC Product\nIND Software\nDUTIES\nD 2 | Run discovery interviews\nREQUIREMENTS\nEND'):
            payload = worker._luna_analysis(self.LINES, self.SCOPES)
        self.assertEqual(payload['provider'], 'luna')
        self.assertEqual([row['text'] for row in payload['duties']], ['Run discovery interviews'])
        with mock.patch.object(luna, 'luna_allowed', return_value=True), \
                mock.patch.object(luna, 'analyse_lines', side_effect=RuntimeError('rate limited')):
            self.assertIsNone(worker._luna_analysis(self.LINES, self.SCOPES))
        with mock.patch.object(luna, 'luna_allowed', return_value=False), \
                mock.patch.object(luna, 'analyse_lines') as called:
            self.assertIsNone(worker._luna_analysis(self.LINES, self.SCOPES))
            called.assert_not_called()

    def test_evidence_falls_back_to_the_local_reranker(self):
        local = {'items': [], 'role': None, 'domain': None, 'context': 'x'}
        with mock.patch.object(luna, 'luna_allowed', return_value=True), \
                mock.patch.object(luna, 'compute_evidence', side_effect=RuntimeError('busy')), \
                mock.patch.object(worker, 'compute_evidence', return_value=local) as fallback:
            result = worker._evidence_with_backup(ANALYSIS, 'PO', SOURCE, b'', worker.embed, worker.rerank)
        self.assertIs(result, local)
        fallback.assert_called_once()
        with mock.patch.object(luna, 'luna_allowed', return_value=True), \
                mock.patch.object(luna, 'compute_evidence', return_value={'provider': 'luna'}):
            self.assertEqual(worker._evidence_with_backup(ANALYSIS, 'PO', SOURCE, b'', worker.embed, worker.rerank),
                             {'provider': 'luna'})
        # An injected reranker (tests, tools) always uses the local path.
        with mock.patch.object(luna, 'luna_allowed', return_value=True) as allowed, \
                mock.patch.object(worker, 'compute_evidence', return_value=local):
            worker._evidence_with_backup(ANALYSIS, 'PO', SOURCE, b'', worker.embed, lambda q, d: [0.0] * len(d))
            allowed.assert_not_called()


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
