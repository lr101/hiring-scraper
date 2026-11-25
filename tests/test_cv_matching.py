"""Regression cases for a broad, evidence-backed project/product CV."""
import unittest
from unittest.mock import patch

import hiring_scraper.matching as matching
from hiring_scraper.matching import enrich_job, extract_profile_skills, plain_text
from hiring_scraper.pages import Document


CV = {
    'desired_roles': ['Project coordinator', 'Product development', 'Learning and development',
                      'Process improvement', 'Innovation engineer'],
    'skills': ['Project management', 'Requirements gathering', 'Product development',
               'Stakeholder coordination', 'Process improvement', 'Training and onboarding',
               'Customer service', 'Excel', 'CAD', 'SolidWorks', 'Fusion 360', 'Figma'],
    'skill_evidence': {'CAD': {'context': 'academic', 'note': 'Engineering degree projects'},
                       'SolidWorks': {'context': 'academic', 'note': 'CNC project'}},
    'languages': ['German', 'English', 'Spanish', 'French'],
    'language_levels': {'German': 'B2', 'English': 'C1', 'Spanish': 'native', 'French': 'A2'},
}


class CvMatchingTests(unittest.TestCase):


    def test_cv_skill_vocabulary_recognizes_work_and_academic_evidence(self):
        skills = extract_profile_skills('Requirements gathering, product development, Figma, Trello, Kanban, '
                                        'process improvement, KPI monitoring, training and onboarding, '
                                        'Fusion 360, 3D printing, SolidWorks and prototyping.')
        for skill in ('Requirements gathering', 'Product development', 'Figma', 'Trello', 'Kanban',
                      'Process improvement', 'KPI monitoring', 'Training and onboarding',
                      'Fusion 360', '3D printing', 'SolidWorks', 'Prototyping'):
            self.assertIn(skill, skills)


    def test_optional_experience_and_employer_history_are_not_requirements(self):
        job = {'title': 'Project Coordinator', 'description':
               'We have 20 years of experience. Your tasks include project coordination. '
               'Three years of experience is a plus. We offer training benefits. ' * 10}
        enriched = enrich_job(job)
        self.assertIsNone(enriched['experience_years'])
        self.assertEqual(enriched['requirements'], [])


    def test_unrelated_required_duration_is_not_experience_or_a_skill_evidence_filter(self):
        for duration_clause in ('willingness to travel for six months',
                                'commitment to a three-year training program'):
            with self.subTest(duration_clause=duration_clause):
                job = {'title': 'Project Coordinator', 'description':
                       f'Requirements: Python skills are required while {duration_clause}. '
                       'Coordinate projects.'}
                enriched = enrich_job(job)
                self.assertIsNone(enriched['experience_years'])
                self.assertTrue(any(row['value'] == 'Python' and row.get('kind') == 'skill'
                                    for row in enriched['requirements']))


    def test_large_html_prefix_does_not_hide_job_description(self):
        html = '<nav>' + ('Navigation item ' * 9000) + '</nav><main><h1>Projektkoordinatorin</h1>'
        html += '<p>German C1 required. Project management.</p></main>'
        extracted = plain_text(html)
        self.assertIn('German C1 required', extracted)


    def test_each_language_uses_its_own_cefr_level(self):
        result = enrich_job({'title': 'Project Coordinator', 'description':
                             'German B2 and English C1 required. Coordinate projects. ' * 12})
        self.assertEqual({row['value']: row['level'] for row in result['languages']},
                         {'German': 'B2', 'English': 'C1'})

    def test_preferred_experience_and_language_remain_optional(self):
        result = enrich_job({'title': 'Project Coordinator', 'description':
                             'German C1 preferred. Three years experience preferred. Coordinate projects. ' * 10})
        self.assertEqual(result['languages'], [])
        self.assertIsNone(result['experience_years'])

    def test_multiple_specialist_requirements_in_one_sentence_are_all_reviewed(self):
        result = enrich_job({'title': 'Projektmanagerin', 'description':
                             'PV and construction experience required. Coordinate projects. ' * 10})
        self.assertEqual({row['value'].lower() for row in result['requirements']}, {'pv', 'construction'})


    def test_optional_and_mandatory_language_clauses_are_separate(self):
        for wording, expected in (
            ('German C1 preferred and English C1 required.', {'English': 'C1'}),
            ('German C1 required and English C2 preferred.', {'German': 'C1'}),
        ):
            with self.subTest(wording=wording):
                enriched = enrich_job({'title': 'Project Coordinator', 'description': wording})
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']}, expected)

    def test_optional_and_mandatory_experience_and_qualifications_are_separate(self):
        enriched = enrich_job({'title': 'Project Coordinator', 'description':
                               '2 years experience preferred and at least 5 years project management experience required. '
                               'Scrum preferred and PMP certification required.'})
        self.assertEqual(enriched['experience_years']['value'], 5)
        self.assertEqual({row['value'] for row in enriched['requirements']}, {'PMP', 'certification'})


    def test_german_tasks_heading_ends_requirement_section(self):
        enriched = enrich_job({'title': 'Project Coordinator', 'description':
                               'Ihr Profil. PMP certification required. Ihre Aufgaben. Wir nutzen Scrum.'})
        self.assertNotIn('Scrum', {row['value'] for row in enriched['requirements']})

    def test_large_html_input_has_bounded_head_and_tail_parsing(self):
        chunks = []
        class RecordingDocument(Document):
            def feed(self, data):
                chunks.append(len(data))
                return super().feed(data)

        html = '<nav>' + ('A' * 300000) + '</nav><p>MiddleMarker</p><nav>' + ('B' * 300000)
        html += '</nav><main>German C1 required. Project management.</main>'
        with patch.object(matching, 'Document', RecordingDocument):
            extracted = plain_text(html)
        self.assertIn('German C1 required', extracted)
        self.assertNotIn('MiddleMarker', extracted)
        self.assertLessEqual(len(extracted), 240001)
        self.assertLessEqual(max(chunks), 120000)


    def test_comma_and_but_separate_optional_from_required_clauses(self):
        for text, expected in (
            ('German C1 preferred, English C1 required.', {'English': 'C1'}),
            ('Deutsch C1 wünschenswert, Englisch C1 erforderlich.', {'English': 'C1'}),
            ('German C1 preferred but English C1 required.', {'English': 'C1'}),
        ):
            with self.subTest(text=text):
                enriched = enrich_job({'title': 'Project Coordinator', 'description': text})
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']}, expected)
        experience = enrich_job({'title': 'Project Coordinator', 'description':
                                 '2 years experience preferred, at least 5 years experience required.'})
        self.assertEqual(experience['experience_years']['value'], 5)
        qualifications = enrich_job({'title': 'Project Coordinator', 'description':
                                     'Scrum preferred, PMP certification required.'})
        self.assertEqual({row['value'] for row in qualifications['requirements']}, {'PMP', 'certification'})


    def test_punctuation_separated_language_levels_remain_explicit(self):
        for wording in ('German - C1 required', 'German: C1 required',
                        'German, C1 required', 'German / C1 required'):
            with self.subTest(wording=wording):
                enriched = enrich_job({'title': 'Project Manager', 'description': wording})
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                                 {'German': 'C1'})

    def test_very_good_german_and_good_english_levels_are_split_from_one_sentence(self):
        enriched = enrich_job({'title': 'Project Manager', 'description':
                               'Du verfügst über sehr gute Deutsch- und gute Englischkenntnisse.'})
        languages = {row['value']: row for row in enriched['languages']}
        self.assertEqual(languages['German']['level'], 'C1')
        self.assertEqual(languages['German']['level_range'], ['C1', 'C2'])
        self.assertEqual(languages['English']['level'], 'B1')
        self.assertEqual(languages['English']['level_range'], ['B1', 'B2'])

    def test_optional_language_clause_stays_separate_from_cefr_prefixed_requirement(self):
        for description in ('English preferred, C1 German required',
                            'French A2 preferred, C1 German required'):
            with self.subTest(description=description):
                enriched = enrich_job({'title': 'Project Manager', 'description': description})
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                                 {'German': 'C1'})


    def test_shared_required_cefr_list_keeps_both_languages(self):
        enriched = enrich_job({'title': 'Project Coordinator', 'description':
                               'German and English C1 required. Coordinate projects. ' * 10})
        self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                         {'German': 'C1', 'English': 'C1'})


    def test_shared_required_list_after_client_language_keeps_its_own_scope(self):
        enriched = enrich_job({'title': 'Project Coordinator', 'description':
                               'German C1 fluency is common among our clients and French and English C1 required.'})
        self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                         {'French': 'C1', 'English': 'C1'})

    def test_comma_language_list_after_independent_scope_keeps_shared_requirement(self):
        for wording in (
            'French A2 preferred, German, English C1 required.',
            'We provide you with onboarding support, German, English C1 required.',
        ):
            with self.subTest(wording=wording):
                enriched = enrich_job({'title': 'Project Coordinator', 'description': wording})
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                                 {'German': 'C1', 'English': 'C1'})


if __name__ == '__main__':
    unittest.main()
