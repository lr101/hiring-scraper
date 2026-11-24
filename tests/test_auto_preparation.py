import unittest


class AutomaticPreparationTests(unittest.TestCase):
    def test_language_uses_posting_and_available_cv(self):
        from hiring_scraper.app.auto_preparation import document_language
        self.assertEqual(document_language('Wir suchen Sie und Ihre Kenntnisse für unsere Aufgaben', {'de': {}, 'en': {}}), 'de')
        self.assertEqual(document_language('We are looking for you and your experience', {'de': {}, 'en': {}}), 'en')
        self.assertEqual(document_language('We are looking for you', {'de': {}}), 'de')
