"""Validation of the voice-only settings consumed by application preparation."""
import unittest

from pydantic import ValidationError

from hiring_scraper.app.application_documents_v2 import localized_preferences
from hiring_scraper.app.profiles import ApplicationPersonalization


class ApplicationVoiceTests(unittest.TestCase):
    def test_three_exemplars_are_allowed_for_each_language(self):
        samples = [{'language': language, 'kind': 'cv_summary', 'text': 'Fictional studio summary.'}
                   for language in ('en', 'de') for _ in range(3)]
        preferences = ApplicationPersonalization(voice_notes='Use short sentences.', style_exemplars=samples)
        self.assertEqual(preferences.model_dump()['style_exemplars'], samples)
        with self.assertRaises(ValidationError):
            ApplicationPersonalization(style_exemplars=samples[:3] + samples[:1])

    def test_voice_and_example_limits(self):
        with self.assertRaises(ValidationError):
            ApplicationPersonalization(voice_notes='x' * 1501)
        with self.assertRaises(ValidationError):
            ApplicationPersonalization(style_exemplars=[
                {'language': 'de', 'kind': 'cover_letter', 'text': 'x' * 16001}])


    def test_shared_voice_settings_survive_a_localized_personalization_override(self):
        shared = {'voice_notes': 'Use short sentences.', 'style_exemplars': [
            {'language': 'de', 'kind': 'cv_summary', 'text': 'Fictional studio summary.'}]}
        preferences = {
            'application_personalization': {**shared, 'career_evidence': 'English example.'},
            'localized_content': {'de': {'application_personalization': {'career_evidence': 'Deutscher Beleg.'}}},
        }
        self.assertEqual(localized_preferences(preferences, 'de')['application_personalization'],
                         {**shared, 'career_evidence': 'Deutscher Beleg.'})
