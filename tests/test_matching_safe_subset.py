"""Safe-subset matcher behaviour: unknown tenure, gap dedupe, headings, soft conflicts."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hiring_scraper import matching
from hiring_scraper.app import job_search
from hiring_scraper.matching import enrich_job
from hiring_scraper.requirements import clause_key, scoped_sentences, structured_text

PROFILE = {'skills': ['Python', 'SQL', 'Docker'], 'desired_roles': ['Software Engineer'],
           'languages': ['English', 'German'], 'language_levels': {'English': 'C2', 'German': 'C1'}}
EN_JOB = {'title': 'Backend Engineer', 'description':
          'Your tasks:\nBuild REST APIs in Python.\nOperate Docker services.\n'
          'Your profile:\n3+ years of experience with Python and SQL. Fluent English.'}
DE_JOB = {'title': 'Backend Entwickler', 'description':
          'Deine Aufgaben:\nEntwicklung von Schnittstellen in Python.\n'
          'Dein Profil: Mindestens 3 Jahre Berufserfahrung mit Python, SQL und Docker. Gute Deutschkenntnisse.'}


class RequirementGapDedupeTests(unittest.TestCase):


    def test_every_row_of_a_clause_shares_the_full_clause_key(self):
        enriched = enrich_job(DE_JOB)
        keys = {row['clause_key'] for row in enriched['requirements']}
        self.assertEqual(len(enriched['requirements']), 3)
        self.assertEqual(keys, {enriched['experience_years']['clause_key']})

    def test_clause_key_keeps_plus_and_hash_and_ignores_headings(self):
        self.assertNotEqual(clause_key('Erfahrung mit C++'), clause_key('Erfahrung mit C#'))
        self.assertNotEqual(clause_key('Erfahrung mit C++'), clause_key('Erfahrung mit C'))
        self.assertEqual(clause_key('Ihr Profil: 3 Jahre  Erfahrung mit Python.'),
                         clause_key('3 Jahre Erfahrung mit Python'))

    def test_distinct_clauses_keep_distinct_gaps(self):
        job = {'title': 'Engineer', 'description':
               'Your profile:\nMandatory 3 years of professional experience in C++.\n'
               'Mandatory 3 years of professional experience in C#.'}
        keys = {row['clause_key'] for row in enrich_job(job)['requirements']}
        self.assertEqual(len(keys), 2)

    def test_evidence_excerpts_snap_to_word_boundaries(self):
        text = ('Rahmenbedingungen und weitere Hinweise zur Stelle, Ihr Profil: ' + 'lorem ipsum dolor ' * 6 +
                'Mindestens 3 Jahre Berufserfahrung mit Python ' + 'consectetur adipiscing elit ' * 6)
        match = next(matching._pattern('Python').finditer(text))
        excerpt = matching._evidence('Python', text, match, 'description')['evidence']
        self.assertIn('Python', excerpt)
        words = set(text.split())
        self.assertTrue(all(word in words for word in excerpt.split()), excerpt)
        self.assertIn(excerpt.strip(), text)


class FlattenedHeadingTests(unittest.TestCase):
    def scopes(self, text):
        return [(sentence, scope) for sentence, scope in scoped_sentences(structured_text(text))]

    def test_flattened_duty_heading_with_company_name_scopes_duties(self):
        scopes = self.scopes('About us We build developer tools in Berlin. '
                             'What you will do at ACME: Develop REST APIs and own services. '
                             'Your profile: Python and SQL')
        self.assertIn(('Develop REST APIs and own services.', 'duties'), scopes)
        self.assertIn(('We build developer tools in Berlin.', 'benefits'), scopes)
        self.assertEqual(scopes[-1][1], 'requirements')

    def test_duty_wording_inside_benefits_prose_keeps_the_benefits_scope(self):
        for text in ('Benefits: A paid course on what you will do with Docker: Kubernetes basics. Your profile: Python',
                     'Benefits We offer a paid course on what you will do at ACME: Docker training and a gym. '
                     'Your profile: Python',
                     'Benefits Ein Kurs zu dem, was du bei uns machst: Docker und Kubernetes. Your profile: Python'):
            with self.subTest(text=text):
                scopes = self.scopes(text)
                docker = [scope for sentence, scope in scopes if 'Docker' in sentence]
                self.assertEqual(docker, ['benefits'])
                self.assertEqual(scopes[-1][1], 'requirements')

    def test_german_and_english_duty_headings_scope_duties(self):
        for heading in ('Das erwartet dich bei uns:', 'Was du bei uns machst:', "What you'll do:"):
            with self.subTest(heading=heading):
                scopes = list(scoped_sentences(f'{heading}\nEntwicklung von Schnittstellen und Betrieb der Dienste.\n'))
                self.assertEqual(scopes[-1][1], 'duties')

    def test_duty_heading_changes_skill_mention_kind(self):
        job = {'title': 'Engineer', 'description':
               'About us We use Python.\nWhat you will do at ACME: Build services in Docker.'}
        kinds = {row['name']: row['kind'] for row in enrich_job(job)['skills']}
        self.assertEqual(kinds.get('Docker'), 'mentioned')


class LocalizedLanguageKeyTests(unittest.TestCase):
    JOB = {'title': 'Shift Supervisor', 'description':
           'What you bring:\n3 years in warehouse operations.\nEnglish at C1 level is required.'}

    def conflicts(self, languages, levels):
        profile = {'skills': ['SAP'], 'desired_roles': ['Shift Supervisor'], 'languages': languages,
                   'language_levels': levels, 'experience_years': 5}
        return match_job(self.JOB, profile)['conflicts']


    def test_canonical_language_maps_known_localized_names_only(self):
        self.assertEqual(matching._canonical_language('Englisch'), 'English')
        self.assertEqual(matching._canonical_language(' deutsch '), 'German')
        self.assertEqual(matching._canonical_language('English'), 'English')
        self.assertEqual(matching._canonical_language('Esperanto'), 'Esperanto')


class MinMatchScoreDefaultTests(unittest.TestCase):
    def allowed(self, defaults, score):
        profile = SimpleNamespace(matching_preferences={'matching_defaults': defaults} if defaults is not None else {})
        with patch.object(job_search, 'geographic_scope', return_value={'eligible': True, 'unknowns': []}):
            return job_search.application_match_allowed(
                SimpleNamespace(), profile, {'eligible': True, 'score': score, 'uncertain': False})

    def test_default_threshold_is_zero(self):
        self.assertTrue(self.allowed(None, 0))
        self.assertTrue(self.allowed({}, 12))
        self.assertTrue(self.allowed({'min_match_score': 'bad'}, 5))

    def test_explicit_threshold_is_still_applied(self):
        self.assertFalse(self.allowed({'min_match_score': 30}, 29))
        self.assertTrue(self.allowed({'min_match_score': 30}, 30))


class VersionTests(unittest.TestCase):
    def test_version_marks_the_safe_subset(self):
        self.assertEqual(matching.VERSION, 'rules-v51-safe-subset')


if __name__ == '__main__':
    unittest.main()
