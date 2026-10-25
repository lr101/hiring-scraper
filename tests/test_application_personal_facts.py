import unittest
from pydantic import ValidationError
from hiring_scraper.app.profiles import ApplicationDetails


class ApplicationPersonalFactsTests(unittest.TestCase):
    def test_explicit_personal_facts_survive_api_validation(self):
        facts = dict(salutation='Herr', birth_name='Beispiel', birth_date='1992-02-29', birth_place='Bonn',
                     nationality='Deutschland', driving_licence='Klasse B', additional_information='Kontakt per E-Mail.')
        details = ApplicationDetails(**facts).model_dump()
        for key, value in facts.items():
            self.assertEqual(details[key], value)
        for key in facts:
            self.assertEqual(ApplicationDetails().model_dump()[key], '')

    def test_birth_date_requires_a_real_complete_iso_date(self):
        for value in ['1991-02-29', '1992-13-01', '29.02.1992', '1992-02', '0000-01-01']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ApplicationDetails(birth_date=value)
