"""Checks that offline evaluation does not reward omitted relevant jobs."""
import hashlib
import unittest

from experiments.cv_profile_evaluation import evaluate, metrics


class ProfileEvaluationTests(unittest.TestCase):
    def test_missing_candidates_reduce_discounted_gain_and_recall(self):
        labels = {'a': {'fit': 'recommended'}, 'b': {'fit': 'possible'},
                  'c': {'fit': 'unlikely'}}
        perfect = metrics([{'id': 'a'}, {'id': 'b'}], labels)
        incomplete = metrics([{'id': 'a'}], labels)
        self.assertEqual(perfect['ndcg_10'], 1)
        self.assertEqual(incomplete['pooled_recall'], .5)
        self.assertLess(incomplete['ndcg_10'], 1)
        self.assertEqual(incomplete['precision_10'], 1)
        self.assertEqual(incomplete['returned_10'], 1)

    def test_no_candidates_does_not_claim_perfect_precision(self):
        result = metrics([], {'a': {'fit': 'possible'}})
        self.assertIsNone(result['pooled_precision'])
        self.assertIsNone(result['precision_10'])
        self.assertEqual(result['pooled_recall'], 0)
        self.assertEqual(result['ndcg_10'], 0)

    def test_changed_job_text_invalidates_its_frozen_judgment(self):
        digest = hashlib.sha256(b'Project coordinator\nOriginal description').hexdigest()
        with self.assertRaisesRegex(ValueError, 'text changed after judgment'):
            evaluate([{'id': 'a', 'title': 'Project coordinator', 'description': 'Changed'}],
                     [{'id': 'a', 'fit': 'possible', 'document_sha256': digest}], {})

    def test_duplicate_judgments_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unique IDs'):
            evaluate([], [{'id': 'a', 'fit': 'possible'},
                          {'id': 'a', 'fit': 'unlikely'}], {})


if __name__ == '__main__':
    unittest.main()
