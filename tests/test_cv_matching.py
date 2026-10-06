"""Regression cases for a broad, evidence-backed project/product CV."""
import unittest
from unittest.mock import patch

import hiring_scraper.matching as matching
from hiring_scraper.matching import enrich_job, extract_profile_skills, match_job, plain_text
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
    def test_cv_roles_match_english_and_gendered_german_titles(self):
        for title in ('Project Coordinator', 'Projektkoordinatorin', 'Projektmanagerin',
                      'PMO Coordinator', 'Produktentwicklerin', 'Personalentwicklung',
                      'Schulungskoordinatorin', 'Prozessoptimierung', 'Innovationsingenieurin'):
            with self.subTest(title=title):
                result = match_job({'title': title, 'description': 'Coordinate projects, requirements and stakeholders. ' * 15}, CV)
                self.assertGreaterEqual(result['score'], 30)
                self.assertTrue(any(reason.startswith('Role matches ') for reason in result['reasons']))

    def test_role_fit_dominates_transferable_mentions_and_title_only_is_possible(self):
        relevant = match_job({'title': 'Projektkoordinatorin',
                              'description': 'Plan projects and coordinate stakeholders. ' * 20}, CV)
        unrelated = match_job({'title': 'Softwareentwickler',
                               'description': 'CAD, Excel, customer service and onboarding. ' * 20}, CV)
        title_only = match_job({'title': 'Projektkoordinatorin'}, CV)
        self.assertEqual(relevant['fit_tier'], 'recommended')
        self.assertEqual(unrelated['fit_tier'], 'unlikely')
        self.assertLess(unrelated['score'], 30)
        self.assertGreater(relevant['score'], unrelated['score'])
        self.assertEqual(title_only['fit_tier'], 'possible')

    def test_extra_cv_skills_do_not_reduce_relevant_job_score(self):
        job = {'title': 'Project Coordinator', 'description': 'Project management and stakeholder coordination. ' * 20}
        small = match_job(job, {'desired_roles': ['Project coordinator'],
                                'skills': ['Project management', 'Stakeholder coordination']})
        rich = match_job(job, {**CV, 'desired_roles': ['Project coordinator']})
        self.assertGreaterEqual(rich['score'], small['score'])

    def test_cv_skill_vocabulary_recognizes_work_and_academic_evidence(self):
        skills = extract_profile_skills('Requirements gathering, product development, Figma, Trello, Kanban, '
                                        'process improvement, KPI monitoring, training and onboarding, '
                                        'Fusion 360, 3D printing, SolidWorks and prototyping.')
        for skill in ('Requirements gathering', 'Product development', 'Figma', 'Trello', 'Kanban',
                      'Process improvement', 'KPI monitoring', 'Training and onboarding',
                      'Fusion 360', '3D printing', 'SolidWorks', 'Prototyping'):
            self.assertIn(skill, skills)

    def test_cefr_gap_conflicts_only_for_explicit_mandatory_level(self):
        job = {'title': 'Project Coordinator', 'description': 'German C1 required. Coordinate projects. ' * 15}
        result = match_job(job, CV)
        self.assertFalse(result['eligible'])
        self.assertTrue(any('German C1' in gap for gap in result['conflicts']))
        optional = match_job({'title': 'Project Coordinator',
                              'description': 'German C1 is a plus. Coordinate projects. ' * 15}, CV)
        self.assertTrue(optional['eligible'])

    def test_vague_fluency_is_a_review_gap_with_known_b2(self):
        result = match_job({'title': 'Project Coordinator',
                            'description': 'Fluent German required. Coordinate projects. ' * 15}, CV)
        self.assertTrue(result['eligible'])
        self.assertTrue(any('German' in gap for gap in result['unknowns']))

    def test_optional_experience_and_employer_history_are_not_requirements(self):
        job = {'title': 'Project Coordinator', 'description':
               'We have 20 years of experience. Your tasks include project coordination. '
               'Three years of experience is a plus. We offer training benefits. ' * 10}
        enriched = enrich_job(job)
        self.assertIsNone(enriched['experience_years'])
        self.assertEqual(enriched['requirements'], [])

    def test_mandatory_experience_and_qualification_are_review_gaps_without_cv_proof(self):
        job = {'title': 'Project Coordinator', 'description':
               'Requirements: at least 3 years of project management experience. '
               'PMP certification required. Coordinate stakeholders. ' * 10}
        result = match_job(job, CV)
        self.assertTrue(result['eligible'])
        self.assertIn('Profile experience not provided', result['unknowns'])
        self.assertTrue(any('PMP' in gap for gap in result['unknowns']))

    def test_academic_skill_match_is_explained_as_academic(self):
        result = match_job({'title': 'Innovation Engineer',
                            'description': 'Prototype concepts using CAD and SolidWorks. ' * 20}, CV)
        self.assertTrue(any('academic' in reason.lower() for reason in result['reasons']))

    def test_secondary_role_is_a_possible_lead_with_lower_weight(self):
        profile = {**CV, 'desired_roles': ['Project coordinator'], 'secondary_roles': ['Executive assistant']}
        secondary = match_job({'title': 'Executive Assistant', 'description': 'Support executives. ' * 30}, profile)
        self.assertEqual(secondary['fit_tier'], 'possible')
        self.assertLess(secondary['score'], 65)

    def test_unverified_specialist_domain_keeps_matching_title_possible(self):
        result = match_job({'title': 'Projektmanagerin',
                            'description': 'At least 3 years of PV construction experience required. '
                                           'Coordinate projects and stakeholders. ' * 12}, CV)
        self.assertEqual(result['fit_tier'], 'possible')
        self.assertTrue(any('PV' in gap for gap in result['unknowns']))

    def test_large_html_prefix_does_not_hide_job_description(self):
        html = '<nav>' + ('Navigation item ' * 9000) + '</nav><main><h1>Projektkoordinatorin</h1>'
        html += '<p>German C1 required. Project management.</p></main>'
        extracted = plain_text(html)
        self.assertIn('German C1 required', extracted)

    def test_german_profile_section_marks_specialist_requirement_for_review(self):
        job = {'title': 'Projektmanagerin', 'description':
               'Ihr Profil: Erfahrung in Photovoltaik und Bauleitung. '
               'Ihre Aufgaben: Sie koordinieren Projekte und Stakeholder. ' * 10}
        result = match_job(job, CV)
        self.assertEqual(result['fit_tier'], 'possible')
        self.assertTrue(any('Photovoltaik' in gap for gap in result['requirement_gaps']))

    def test_benefits_and_unrelated_roles_do_not_turn_transferable_skills_into_a_recommendation(self):
        for title in ('Sales Manager', 'Pflegefachkraft'):
            with self.subTest(title=title):
                result = match_job({'title': title, 'description':
                                    'Our benefits include training and onboarding. We use Excel. '
                                    'Customer service and CAD are mentioned by our team. ' * 15}, CV)
                self.assertEqual(result['fit_tier'], 'unlikely')
                self.assertLess(result['score'], 30)

    def test_profile_heading_applies_to_following_specialist_sentence(self):
        result = match_job({'title': 'Projektmanagerin', 'description':
                            'Ihr Profil. Erfahrung in Elektrotechnik. '
                            'Ihre Aufgaben. Sie koordinieren Projekte und Stakeholder. ' * 10}, CV)
        self.assertEqual(result['fit_tier'], 'possible')
        self.assertTrue(any('Elektrotechnik' in gap for gap in result['requirement_gaps']))

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

    def test_client_language_mention_is_not_a_candidate_requirement(self):
        result = match_job({'title': 'Project Coordinator',
                            'description': 'German C1 is spoken by our clients. Coordinate projects. ' * 12}, CV)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['conflicts'], [])

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

    def test_benefits_only_skill_overlap_keeps_matching_title_possible(self):
        result = match_job({'title': 'Project Coordinator',
                            'description': 'Our benefits include Excel training, flexible hours and lunch vouchers. ' * 12},
                           {'desired_roles': ['Project coordinator'], 'skills': ['Excel']})
        self.assertEqual(result['fit_tier'], 'possible')
        self.assertLess(result['score'], 65)

    def test_role_duty_skill_outweighs_earlier_benefit_mention(self):
        result = match_job({'title': 'Project Coordinator', 'description':
                            'Our benefits include Excel training. Your duties require Excel reporting. ' * 12},
                           {'desired_roles': ['Project coordinator'], 'skills': ['Excel']})
        self.assertEqual(result['fit_tier'], 'recommended')

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

    def test_client_fluency_context_does_not_require_candidate_cefr(self):
        result = match_job({'title': 'Project Coordinator',
                            'description': 'German C1 fluency is common among our clients. Coordinate projects. ' * 12}, CV)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['conflicts'], [])
        required = match_job({'title': 'Project Coordinator',
                              'description': 'Fluent German C1 required to coordinate projects. ' * 12}, CV)
        self.assertFalse(required['eligible'])

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

    def test_received_training_is_incidental_while_delivered_training_is_role_evidence(self):
        profile = {'desired_roles': ['Learning and development'], 'skills': ['Training and development']}
        benefit = match_job({'title': 'Learning and Development Coordinator',
                             'description': 'You receive training and development as part of our perks. ' * 12}, profile)
        duty = match_job({'title': 'Learning and Development Coordinator',
                          'description': 'You deliver training and development programs to employees. ' * 12}, profile)
        self.assertEqual(benefit['fit_tier'], 'possible')
        self.assertEqual(duty['fit_tier'], 'recommended')
        excel = match_job({'title': 'Project Coordinator',
                           'description': 'You receive Excel training as part of our perks. ' * 12},
                          {'desired_roles': ['Project coordinator'], 'skills': ['Excel']})
        self.assertEqual(excel['fit_tier'], 'possible')

    def test_explicit_vague_fluency_requirements_stay_review_gaps(self):
        for wording in ('German fluency required.', 'Deutschkenntnisse fließend erforderlich.'):
            with self.subTest(wording=wording):
                result = match_job({'title': 'Project Coordinator',
                                    'description': (wording + ' Coordinate projects. ') * 12}, CV)
                self.assertTrue(result['eligible'])
                self.assertTrue(any('German fluency' in gap for gap in result['unknowns']))

    def test_benefit_clause_does_not_suppress_separate_required_language(self):
        for wording in ('We provide you with onboarding support, but German C1 is required.',
                        'You receive onboarding support, but German C1 is required.'):
            with self.subTest(wording=wording):
                result = match_job({'title': 'Project Coordinator',
                                    'description': (wording + ' Coordinate projects. ') * 12}, CV)
                self.assertFalse(result['eligible'])
                self.assertTrue(any('German C1' in gap for gap in result['conflicts']))

    def test_custom_skill_title_does_not_corroborate_itself(self):
        profile = {'desired_roles': ['Elixir Engineer'], 'skills': ['Elixir']}
        benefit = match_job({'title': 'Elixir Engineer',
                             'description': 'Modern office in the city center, with flexible hours. ' * 12}, profile)
        duty = match_job({'title': 'Elixir Engineer',
                          'description': 'You build services with Elixir. ' * 12}, profile)
        self.assertEqual(benefit['fit_tier'], 'possible')
        self.assertEqual(benefit['custom_skill_evidence'][0]['source'], 'title')
        self.assertEqual(duty['fit_tier'], 'recommended')
        self.assertEqual(duty['custom_skill_evidence'][0]['source'], 'description')

    def test_shared_required_cefr_list_keeps_both_languages(self):
        enriched = enrich_job({'title': 'Project Coordinator', 'description':
                               'German and English C1 required. Coordinate projects. ' * 10})
        self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                         {'German': 'C1', 'English': 'C1'})

    def test_benefit_boundary_preserves_shared_required_language_list(self):
        for wording in (
            'We provide you with onboarding support, but German and English C1 required.',
            'You receive onboarding support but German and English C1 required.',
            'Wir bieten Unterstützung, aber Deutsch und Englisch C1 erforderlich.',
        ):
            with self.subTest(wording=wording):
                job = {'title': 'Project Coordinator', 'description': wording}
                enriched = enrich_job(job)
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                                 {'German': 'C1', 'English': 'C1'})
                result = match_job(job, CV, enriched)
                self.assertFalse(result['eligible'])
                self.assertTrue(any('German C1' in gap for gap in result['conflicts']))

    def test_preference_boundary_preserves_shared_required_language_list(self):
        for wording in (
            'French A2 preferred, German and English C1 required.',
            'Französisch A2 wünschenswert, Deutsch und Englisch C1 erforderlich.',
        ):
            with self.subTest(wording=wording):
                job = {'title': 'Project Coordinator', 'description': wording}
                enriched = enrich_job(job)
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                                 {'German': 'C1', 'English': 'C1'})
                result = match_job(job, CV, enriched)
                self.assertFalse(result['eligible'])
                self.assertTrue(any('German C1' in gap for gap in result['conflicts']))


if __name__ == '__main__':
    unittest.main()
