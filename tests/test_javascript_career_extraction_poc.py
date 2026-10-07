import unittest

from experiments import javascript_career_extraction_poc


class JavaScriptCareerExtractionPocTests(unittest.TestCase):
    def test_saved_trust_metadata_admits_only_first_party_or_branded_card_rows(self):
        """Changing the allow-list to admit an unverified source must fail this test."""
        cards = [{"title": "Platform Engineer", "url": "https://jobs.example/42"}]
        admit = getattr(javascript_career_extraction_poc, "_active_cards_from_saved_trust", None)

        self.assertIsNotNone(admit, "the replay needs a saved-trust admission step")
        if admit is None:
            return

        self.assertEqual(
            admit({"html_extraction_trust": "first_party"}, cards),
            (cards, "first_party"),
        )
        self.assertEqual(
            admit(
                {"html_extraction_trust": "branded_external"}, cards
            ),
            (cards, "branded_external"),
        )
        self.assertEqual(
            admit(
                {"html_extraction_trust": "unverified_external_source"}, cards
            ),
            ([], "unverified_external_source"),
        )
