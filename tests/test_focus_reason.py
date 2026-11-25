"""The focus reason names the match tier honestly; it never calls a weak lead strong."""
import unittest

from hiring_scraper.app.focus import _reason


class FocusReasonTests(unittest.TestCase):
    def test_strong_label_requires_the_recommended_score(self):
        self.assertEqual(_reason('new', False, 65), 'Strong match (65)')
        self.assertEqual(_reason('new', False, 100), 'Strong match (100)')

    def test_middling_scores_get_a_neutral_label(self):
        self.assertEqual(_reason('new', False, 30), 'Possible match (30)')
        self.assertEqual(_reason('new', False, 64), 'Possible match (64)')

    def test_zero_low_or_missing_scores_never_claim_a_match(self):
        for score in (0, 12, 29, None):
            with self.subTest(score=score):
                self.assertEqual(_reason('new', False, score), 'New in your Journey')

    def test_readiness_and_interest_come_first(self):
        self.assertEqual(_reason('new', True, 90), 'Documents are ready to review and send')
        self.assertEqual(_reason('open', False, 90), 'You marked this role as Interested')


if __name__ == '__main__':
    unittest.main()
