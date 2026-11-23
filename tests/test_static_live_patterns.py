"""Sanitized patterns reproduced from read-only live job review; no applicant identity."""
import unittest
from hiring_scraper.matching import enrich_job, match_job


class LiveRequirementPatterns(unittest.TestCase):
    def test_flattened_candidate_sections_are_not_swallowed_by_about_us(self):
        from hiring_scraper.requirements import scoped_sentences, structured_text
        rows = list(scoped_sentences(structured_text(
            'About us We build interesting products for our customers in many countries '
            'Dein Profil Du bringst Erfahrung mit Deine Aufgaben Du erstellst Arbeitsanweisungen. '
            'Das bieten wir Du bekommst Schulungen.')) )
        self.assertIn(('Du erstellst Arbeitsanweisungen.', 'duties'), rows)
        self.assertIn(('Du bringst Erfahrung mit', 'requirements'), rows)

    def test_flattened_sections_preserve_required_optional_and_employer_scope(self):
        e = enrich_job({'title': 'Coordinator', 'description':
            'About us We develop Python and SQL services. Your responsibilities Coordinate workshops. '
            'Required qualifications German C1 required. Nice to have PMP certification. Benefits Training provided.'})
        self.assertTrue(any(r['value'] == 'German' and r['level'] == 'C1' for r in e['languages']))
        self.assertFalse(any(r['value'] == 'PMP' for r in e['requirements']))

    def test_explicit_german_level_after_inflected_linking_words(self):
        for text in ('Deine Deutschkenntnisse sind mindestens auf C1-Niveau.',
                     'Du hast sehr gute Deutsch- und Englischkenntnisse (Niveau C1).',
                     'Du verfügst über sehr gute Deutsch- und Englischkenntnisse (beides C1 Niveau).'):
            with self.subTest(text=text):
                r = match_job({'title': 'Coordinator', 'description': text}, {'language_levels': {'German': 'B2', 'English': 'C1'}})
                self.assertEqual(r['gates']['language']['status'], 'fail')

    def test_experience_alternatives_do_not_become_cumulative_minima(self):
        job = {'title': 'Coordinator', 'description': 'Requirements:\n3 years of project management experience or 5 years of operations experience required.'}
        e = enrich_job(job)
        self.assertEqual(e['experience_years']['value'], 3)
        self.assertTrue(match_job(job, {'experience_years': 3}, e)['eligible'])
        mixed = enrich_job({'description': 'Requirements:\n7 years of project management experience and 3 years of technical experience or 4 years of research experience required.'})
        self.assertEqual(mixed['experience_years']['value'], 7)

    def test_optional_hardware_background_does_not_waive_seven_year_minimum(self):
        e = enrich_job({'title': 'Project coordinator', 'description':
            'Required skills & experience Experience: 7+ years project/program management in complex R&D environments '
            '(hardware systems preferred). Have at least worked 3+ years in technical execution.'})
        self.assertEqual(e['experience_years']['value'], 7)

    def test_enrollment_certificate_is_a_current_student_prerequisite(self):
        r = match_job({'title': 'Research thesis', 'description':
            'Bitte reiche folgende Unterlagen ein: Aktuelle Immatrikulationsbescheinigung.'}, {})
        self.assertTrue(r['mandatory_review'])
        self.assertTrue(any(c['kind'] == 'enrolment' for c in r['prerequisite_conditions']))

    def test_total_tenure_does_not_establish_hardware_tenure(self):
        r = match_job({'title': 'Prototyping specialist', 'description':
            'Required qualifications:\n3+ years of experience in mechanical prototyping and hardware assembly.'},
            {'experience_years': 5, 'skills': ['CAD', 'Prototyping']})
        self.assertLess(r['score'], 65)
        # Unverifiable scoped tenure is a visible verify note, not an unknown gate.
        notes = r['unknowns'] + r.get('soft_conflicts', [])
        self.assertTrue(any('scoped' in s.lower() for s in notes))

    def test_construction_business_experience_is_not_project_coordination(self):
        r = match_job({'title': 'Delivery specialist', 'description':
            'Ihr Profil: Mehrjährige Erfahrung im Baugeschäft ist erforderlich.\n'
            'Ihre Aufgaben: Projektplanung, Kalkulation und Steuerung von Bauprojekten.'},
            {'desired_roles': ['Delivery specialist'], 'skills': ['Project management', 'Excel']})
        self.assertTrue(r['requirement_gaps'])
        self.assertLess(r['score'], 65)

    def test_explicit_language_conflict_limits_numeric_shortlist_score(self):
        r = match_job({'title': 'Coordinator', 'description':
            'Your tasks:\nMaintain issue registers. Create status presentations.\nRequirements:\nGerman C1 required.'},
            {'summary': 'Maintained issue registers. Created status presentations.', 'language_levels': {'German': 'B2'}})
        self.assertLess(r['score'], 30)
        self.assertGreaterEqual(r['task_fit'], 65)

    def test_ordinary_product_portfolio_is_not_an_application_work_sample(self):
        r = match_job({'title': 'Coordinator', 'description':
            'Required qualifications You have relevant experience. What’s in it for you '
            'You will broaden your remit across the product portfolio.'}, {})
        self.assertFalse(any(c['kind'] == 'application_artifact' for c in r['prerequisite_conditions']))

    def test_malformed_skill_context_cannot_break_matching(self):
        result = match_job({'title': 'Specialist', 'description': 'Build Python services.'},
            {'skill_evidence': {'Python': {'context': [], 'note': 'Built Python services.'}}})
        self.assertIn('score', result)

    def test_transferable_education_product_role_survives_unproven_ownership(self):
        r = match_job({'title': 'Product manager', 'description':
            'Your tasks:\nOwn a course portfolio. Develop new learning products.\n'
            'Requirements:\nFirst junior product experience. Learning and development experience. '
            'User research and process improvement. Incomplete checklists welcome.'},
            {'skills': ['Training and development', 'User research', 'Process improvement'],
             'summary': 'Designed onboarding training programs. Improved learning processes.'})
        self.assertGreaterEqual(r['score'], 30)
        self.assertNotEqual(r['fit_tier'], 'recommended')
