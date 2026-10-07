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

    def test_block_sections_keep_optional_benefits_and_duties_out_of_requirements(self):
        descriptions = (
            '## Dein Profil\n- French A2 preferred\n- Deutsch C1\n- Abgeschlossenes Studium der Elektrotechnik\n## Deine Aufgaben\n- SAP und CAD einsetzen\n## Wir bieten\n- PMP certification und Deutsch C2 training',
            '<h2>Dein Profil</h2><ul><li>French A2 preferred</li><li>Deutsch C1</li><li>Abgeschlossenes Studium der Elektrotechnik</li></ul><h2>Deine Aufgaben</h2><p>SAP und CAD einsetzen</p><h2>Wir bieten</h2><p>PMP certification und Deutsch C2 training</p>',
        )
        for description in descriptions:
            with self.subTest(description=description):
                job = {'title': 'Projektleiterin', 'description': description}
                enriched = enrich_job(job)
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                                 {'German': 'C1'})
                result = match_job(job, CV, enriched)
                self.assertFalse(result['eligible'])
                self.assertTrue(any('Elektrotechnik' in gap for gap in result['requirement_gaps']))
                self.assertFalse(any('SAP' in gap or 'PMP' in gap for gap in result['requirement_gaps']))

    def test_generic_bachelor_verified_but_specialist_degree_remains_gap(self):
        profile = {**CV, 'education': [{'level': 'bachelor', 'field': 'Innovation and Development Engineering'}]}
        for degree, expected_gap in (
            ('Bachelor degree', False), ('abgeschlossenes Studium', False),
            ('degree in electrical engineering', True), ('Master degree', True),
        ):
            with self.subTest(degree=degree):
                result = match_job({'title': 'Project Coordinator', 'description':
                                    'Your qualifications:\n' + degree + '\nYour tasks:\n' +
                                    'Project management and stakeholder coordination. ' * 20}, profile)
                self.assertEqual(bool(result['requirement_gaps']), expected_gap)
                if not expected_gap:
                    self.assertTrue(any('education' in reason.lower() for reason in result['reasons']))

    def test_separate_mandatory_tool_domain_and_professional_tenure_gaps(self):
        for requirement in ('Expert SAP knowledge', 'Professional CAD experience',
                            'Insurance underwriting experience', 'Pharmaceutical GMP experience'):
            with self.subTest(requirement=requirement):
                result = match_job({'title': 'Project Manager', 'description':
                                    'Your qualifications:\n' + requirement + '\nResponsibilities:\n' +
                                    'Project management and stakeholder coordination. ' * 20}, CV)
                self.assertTrue(result['requirement_gaps'])
                self.assertLess(result['score'], 65)
                self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_experience_range_keeps_minimum_and_unknown_cv_years_reviewable(self):
        job = {'title': 'Project Manager', 'description':
               'Requirements:\n3–5 years of project management experience\nResponsibilities:\n' +
               'Project management and stakeholder coordination. ' * 20}
        enriched = enrich_job(job)
        self.assertEqual(enriched['experience_years']['value'], 3)
        unknown = match_job(job, CV, enriched)
        self.assertTrue(unknown['eligible'])
        self.assertTrue(unknown['requirement_gaps'])
        self.assertLess(unknown['score'], 65)
        self.assertTrue(match_job(job, {**CV, 'experience_years': 3}, enriched)['eligible'])

    def test_student_enrolment_is_not_inferred_from_past_degree(self):
        result = match_job({'title': 'Werkstudent Projektmanagement', 'description':
                            'Ihr Profil:\nImmatrikuliert an einer Hochschule\nIhre Aufgaben:\n' +
                            'Project management and stakeholder coordination. ' * 20},
                           {**CV, 'education': [{'level': 'bachelor', 'field': 'Engineering'}]})
        self.assertTrue(result['eligible'])
        self.assertTrue(any('enrol' in gap.lower() for gap in result['requirement_gaps']))
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_measured_role_aliases_preserve_career_focus(self):
        profile = {**CV, 'desired_roles': CV['desired_roles'] + ['Program coordinator', 'Business analyst']}
        for title in ('Projektleiter', 'Projektassistenz', 'Project Delivery Manager',
                      'Business Process Analyst', 'Programme Assistant', 'Assistenz Geschäftsleitung',
                      'Innovationsassistent', 'Projektingenieur'):
            with self.subTest(title=title):
                result = match_job({'title': title, 'description':
                                    'Project management and stakeholder coordination. ' * 20}, profile)
                self.assertTrue(any(reason.startswith('Role matches ') for reason in result['reasons']))

    def test_one_generic_cv_signal_is_not_a_perfect_role_match(self):
        result = match_job({'title': 'Project Coordinator', 'description':
                            'Customer service for highly specialized infrastructure projects. ' * 20}, CV)
        self.assertLess(result['score'], 100)
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_applicant_qualification_heading_word_orders_and_completed_degree_cues(self):
        for heading in ('Das bringst du mit', 'Was bringen Sie mit?',
                        'Überzeuge uns mit deinen Qualifikationen', 'Your qualifications'):
            with self.subTest(heading=heading):
                result = match_job({'title': 'Development Engineer', 'description':
                                    '**' + heading + '**\n- Abgeschlossenes Masterstudium in Maschinenbau\n'
                                    '- Mehrjährige Berufserfahrung mit CAD\n**Ihre Aufgaben**\n' +
                                    'Product development and stakeholder coordination. ' * 20}, CV)
                self.assertTrue(any('Masterstudium' in gap for gap in result['requirement_gaps']))
                self.assertNotEqual(result['fit_tier'], 'recommended')
        result = match_job({'title': 'Project Manager', 'description':
                            'DU…\n- hast ein abgeschlossenes Studium in Pharmazie\n- verfügst über Berufserfahrung in der pharmazeutischen Industrie\n' +
                            'Project management and stakeholder coordination. ' * 20}, CV)
        self.assertTrue(result['requirement_gaps'])
        self.assertLess(result['score'], 65)

    def test_unknown_degree_field_and_degree_alternatives_are_not_generic_degrees(self):
        profile = {**CV, 'education': [{'level': 'bachelor', 'field': 'Innovation and Development Engineering'}]}
        for degree, has_gap in (
            ('Bachelor degree in archaeology', True),
            ('degree in mechanical engineering or innovation and development engineering', False),
            ('Master degree or Bachelor degree', False),
        ):
            with self.subTest(degree=degree):
                result = match_job({'title': 'Project Coordinator', 'description':
                                    'Qualifications:\n' + degree + '\nTasks:\n' +
                                    'Project management and stakeholder coordination. ' * 20}, profile)
                self.assertEqual(bool(result['requirement_gaps']), has_gap)

    def test_sparse_specialist_title_explains_domain_uncertainty(self):
        result = match_job({'title': 'Projektleiter Elektrotechnik'}, CV)
        self.assertTrue(result['eligible'])
        self.assertTrue(any('Elektrotechnik' in gap for gap in result['requirement_gaps']))
        self.assertLess(result['score'], match_job({'title': 'Projektleiter'}, CV)['score'])

    def test_detail_hydration_keeps_qualification_blocks(self):
        import json
        url = 'https://example.org/jobs/1'
        description = '<h2>Your qualifications</h2><ul><li>French A2 preferred</li><li>German C1</li></ul>'
        posting = {'@type': 'JobPosting', 'url': url, 'title': 'Project Coordinator',
                   'description': description}
        updated = matching.detail_updates({'title': 'Project Coordinator', 'url': url},
                                         '<script type="application/ld+json">' + json.dumps(posting) + '</script>', url)
        self.assertIn('\n', updated['description'])
        self.assertFalse(match_job({'title': 'Project Coordinator', **updated}, CV)['eligible'])

    def test_one_known_certificate_does_not_verify_a_different_mandatory_credential(self):
        profile = {**CV, 'certifications': ['SolidWorks Mechanical Design Associate']}
        result = match_job({'title': 'Project Coordinator', 'description':
                            'Requirements:\nPMP and SolidWorks Mechanical Design Associate certification\nResponsibilities:\n' +
                            'Project management and stakeholder coordination. ' * 20}, profile)
        self.assertTrue(any('PMP' in gap for gap in result['requirement_gaps']))

    def test_numeric_professional_cad_requirement_does_not_use_academic_exposure(self):
        result = match_job({'title': 'Project Coordinator', 'description':
                            'Requirements:\nAt least 3 years of professional CAD experience\nResponsibilities:\n' +
                            'Project management and stakeholder coordination. ' * 20},
                           {**CV, 'experience_years': 3})
        self.assertTrue(any('CAD' in gap for gap in result['requirement_gaps']))
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_preferred_qualification_heading_is_optional_until_new_mandatory_heading(self):
        for description, levels in (
            ('Qualifications:\nPreferred qualifications:\nGerman C1\nTasks:\n', {}),
            ('Qualifications:\nPreferred qualifications:\nGerman C1\nRequired qualifications:\nEnglish C1\nTasks:\n', {'English': 'C1'}),
        ):
            with self.subTest(description=description):
                job = {'title': 'Project Coordinator', 'description': description +
                       'Project management and stakeholder coordination. ' * 20}
                enriched = enrich_job(job)
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']}, levels)
                self.assertTrue(match_job(job, CV, enriched)['eligible'])

    def test_independent_degree_levels_keep_missing_master_gap(self):
        profile = {**CV, 'education': [{'level': 'bachelor', 'field': 'Innovation and Development Engineering'}]}
        job = {'title': 'Project Coordinator', 'description':
               'Qualifications:\nBachelor degree\nMaster degree\nTasks:\n' +
               'Project management and stakeholder coordination. ' * 20}
        enriched = enrich_job(job)
        self.assertEqual({row['level'] for row in enriched['requirements'] if row['kind'] == 'education'},
                         {'bachelor', 'master'})
        result = match_job(job, profile, enriched)
        self.assertTrue(any('Master' in gap for gap in result['requirement_gaps']))
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_degree_does_not_suppress_independent_gmp_experience(self):
        profile = {**CV, 'education': [{'level': 'bachelor', 'field': 'Innovation and Development Engineering'}]}
        job = {'title': 'Project Coordinator', 'description':
               'Qualifications:\nBachelor degree and GMP experience\nTasks:\n' +
               'Project management and stakeholder coordination. ' * 20}
        result = match_job(job, profile)
        self.assertTrue(any('GMP' in gap for gap in result['requirement_gaps']))
        self.assertNotEqual(result['fit_tier'], 'recommended')
        education = enrich_job({'description': 'Qualifications:\nDegree in electrical engineering\nTasks:\n'})
        self.assertEqual([row['kind'] for row in education['requirements']], ['education', 'skill'])

    def test_strongest_independent_mandatory_language_level_is_kept(self):
        for text in ('German B2\nGerman C1', 'German C1\nGerman B2',
                     'German B2\nGerman C1 preferred'):
            with self.subTest(text=text):
                job = {'title': 'Project Coordinator', 'description': 'Qualifications:\n' + text + '\nTasks:\n'}
                result = match_job(job, CV)
                self.assertEqual(result['eligible'], 'preferred' in text)

    def test_language_or_requires_one_satisfied_alternative_and_preserves_conjunction(self):
        for text, profile, eligible in (
            ('German C1 or English C1', CV, True),
            ('Deutsch C1 oder Englisch C1', CV, True),
            ('German C1 or English C2', CV, False),
            ('German C1 and English C1', CV, False),
            ('German C1 or English C1\nGerman C1', CV, False),
            ('German C1 or English C1', {**CV, 'language_levels': {'German': 'B2'}}, True),
        ):
            with self.subTest(text=text, profile=profile):
                job = {'title': 'Project Coordinator', 'description': 'Qualifications:\n' + text + '\nTasks:\n'}
                result = match_job(job, profile)
                self.assertEqual(result['eligible'], eligible)
                if eligible and profile['language_levels'].get('English') == 'C1':
                    self.assertFalse(any('German C1' in conflict for conflict in result['conflicts']))

    def test_soft_wrapped_clinical_qualification_bullets_remain_requirements(self):
        profile = {**CV, 'education': [{'level': 'bachelor', 'field': 'Innovation and Development Engineering'}]}
        result = match_job({'title': 'Projektkoordinator', 'description':
                            '- Unterstützung bei der Erstellung von GMP- und\nGCP-relevanten SOPs\n\n'
                            '- Abgeschlossenes naturwissenschaftliches oder heilberufliches\nStudium (Bachelor oder duales Studium)\n'
                            '- Erfahrung im Bereich der Entwicklung oder im GMP-/GCP-Umfeld\n'
                            '- Kenntnisse der GMP-/GCP-Regularien\n\n' +
                            'Project management and stakeholder coordination. ' * 20}, profile)
        self.assertTrue(any('GMP' in gap for gap in result['requirement_gaps']))
        self.assertTrue(any('naturwissenschaftliches' in gap for gap in result['requirement_gaps']))
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_parenthetical_minimum_cefr_survives_abbreviation_and_unknown_heading(self):
        job = {'title': 'Senior Projektmanager', 'description':
               '**Lust bekommen?** Wenn Du die folgenden Hard Facts mitbringst:\n'
               '* Sehr gute Deutschkenntnisse (mind. C1) und gute Englischkenntnisse (mind. B2)\n' +
               'Project management and stakeholder coordination. ' * 20}
        result = match_job(job, CV)
        self.assertFalse(result['eligible'])
        self.assertTrue(any('German C1' in conflict for conflict in result['conflicts']))

    def test_preferred_degree_specialty_and_optional_english_do_not_remove_mandatory_base(self):
        job = {'title': 'Technischer Projektleiter', 'description':
               'Ihr Profil:\n- Elektrotechnik: Studium der Elektrotechnik, bevorzugt im Bereich Energie- oder Automatisierungstechnik, alternativ eine Weiterbildung zum staatlich geprüften Techniker der Elektrotechnik\n'
               '- Kenntnisse mit sehr guten Deutschkenntnisse (C1 - Level) - gute Englischkenntnisse sind von Vorteil\n' +
               'Project management and stakeholder coordination. ' * 20}
        result = match_job(job, {**CV, 'education': [{'level': 'bachelor', 'field': 'Innovation and Development Engineering'}]})
        self.assertTrue(any('Elektrotechnik' in gap for gap in result['requirement_gaps']))
        self.assertFalse(result['eligible'])
        self.assertTrue(any('German C1' in conflict for conflict in result['conflicts']))

    def test_formal_building_automation_qualification_is_a_specialist_gap(self):
        job = {'title': 'Projektleiter Gebäudeautomation', 'description':
               'Ihre Aufgaben bei uns\n- Werkpläne in AutoCAD erstellen\n\nDamit begeistern Sie uns\n'
               '- Meister, Techniker oder Ingenieur im Bereich der Automatisierungstechnik, Gebäudeautomation, Versorgungstechnik\n'
               '- Projekterfahrung in der Lüftungs-, Kälte- und Heizungstechnik\n'
               '- Erfahrung in der Steuer- und Regelungstechnik für HKL-Anlagen\nDafür bieten wir Ihnen\n' +
               'Project management and stakeholder coordination. ' * 20}
        result = match_job(job, CV)
        self.assertTrue(result['requirement_gaps'])
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_physician_training_title_does_not_match_learning_and_development(self):
        for title in ('Arzt in Weiterbildung für Allgemeinmedizin',
                      'Facharzt Angiologie / Arzt in Weiterbildung Kardiologie'):
            with self.subTest(title=title):
                self.assertEqual(match_job({'title': title}, CV)['fit_tier'], 'unlikely')
        self.assertGreaterEqual(match_job({'title': 'Weiterbildung Koordinator'}, CV)['score'], 30)

    def test_unnamed_c2_language_stays_explicit_review_gap(self):
        result = match_job({'title': 'Projektkoordinator', 'description':
                            'Abgeschlossenes Hochschulstudium. Sprachniveau C2. ' +
                            'Project management and stakeholder coordination. ' * 20}, CV)
        self.assertTrue(any('C2' in gap for gap in result['requirement_gaps']))
        self.assertTrue(result['eligible'])
        self.assertFalse(any('German C2' in conflict for conflict in result['conflicts']))

    def test_senior_title_with_unknown_relevant_tenure_needs_review(self):
        result = match_job({'title': 'Senior Projektmanager', 'description':
                            'Project management and stakeholder coordination. ' * 20}, CV)
        self.assertEqual(result['fit_tier'], 'possible')
        self.assertTrue(any('tenure' in gap for gap in result['requirement_gaps']))

    def test_digital_and_event_coordination_positives_keep_supported_qualification_alternatives(self):
        profile = {**CV, 'education': [{'level': 'bachelor', 'field': 'Innovation and Development Engineering'}]}
        for description in (
            'Deine Aufgaben:\nAnforderungsanalyse und Projektmanagement\nMust-have:\nDu verfügst über eine erfolgreich abgeschlossene Berufsausbildung oder ein Studium\nNice-to-have:\nErfahrung in der Softwareentwicklung\n',
            'Ihr Aufgabenbereich:\nVeranstaltungen und Trainings koordinieren\nIhre Qualifikation:\nEine erfolgreich abgeschlossene Ausbildung oder ein Studium oder eine vergleichbare Qualifikation\nWas wir bieten:\nWeiterbildung\n',
        ):
            with self.subTest(description=description):
                result = match_job({'title': 'Projektkoordinator', 'description': description +
                                    'Project management and stakeholder coordination. ' * 20}, profile)
                self.assertTrue(result['eligible'])
                self.assertNotEqual(result['fit_tier'], 'unlikely')

    def test_completed_degree_fallback_preserves_explicit_non_applicant_scopes(self):
        for heading in ('Preferred qualifications', 'Tasks', 'Benefits'):
            with self.subTest(heading=heading):
                job = {'title': 'Project Coordinator', 'description':
                       'Qualifications:\n' + heading + ':\nAbgeschlossenes Studium der Elektrotechnik\nGerman C1\nTasks:\n'}
                enriched = enrich_job(job)
                result = match_job(job, CV, enriched)
                self.assertEqual(enriched['languages'], [])
                self.assertEqual(enriched['requirements'], [])
                self.assertTrue(result['eligible'])

    def test_neutral_completed_degree_fallback_still_starts_applicant_scope(self):
        job = {'title': 'Project Coordinator', 'description':
               'Abgeschlossenes Studium der Elektrotechnik\nGerman C1\nTasks:\n'}
        enriched = enrich_job(job)
        self.assertTrue(any(row['kind'] == 'education' for row in enriched['requirements']))
        self.assertFalse(match_job(job, CV, enriched)['eligible'])

    def test_two_generic_transferable_skills_do_not_recommend_project_role(self):
        result = match_job({'title': 'Project Manager', 'description':
                            'Customer service and onboarding. ' * 20},
                           {**CV, 'skills': CV['skills'] + ['Onboarding']})
        self.assertNotEqual(result['fit_tier'], 'recommended')
        self.assertLess(result['score'], 65)

    def test_generic_guard_preserves_customer_role_and_substantive_project_evidence(self):
        customer = match_job({'title': 'Customer Service Manager', 'description':
                              'Customer service and onboarding. ' * 20},
                             {**CV, 'desired_roles': ['Customer service']})
        project = match_job({'title': 'Project Manager', 'description':
                             'Project management, requirements gathering, customer service and onboarding. ' * 20},
                            {**CV, 'skills': CV['skills'] + ['Onboarding']})
        self.assertEqual(customer['fit_tier'], 'recommended')
        self.assertEqual(project['fit_tier'], 'recommended')

    def test_required_language_list_stops_at_explicit_contrast(self):
        for wording in (
            'Your qualifications: English C1, German C1, but French A2 preferred.',
            'Ihr Profil: Englisch C1, Deutsch C1, aber Französisch A2 wünschenswert.',
            'Ihr Profil: Englisch C1, Deutsch C1, jedoch Französisch A2 wünschenswert.',
        ):
            with self.subTest(wording=wording):
                job = {'title': 'Project Coordinator', 'description': wording}
                enriched = enrich_job(job)
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                                 {'English': 'C1', 'German': 'C1'})
                self.assertFalse(match_job(job, CV, enriched)['eligible'])

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

    def test_and_boundary_preserves_shared_required_language_list(self):
        for wording in (
            'French A2 preferred and German and English C1 required.',
            'We provide you with onboarding support and German and English C1 required.',
            'Französisch A2 wünschenswert und Deutsch und Englisch C1 erforderlich.',
            'Wir bieten Unterstützung und Deutsch und Englisch C1 erforderlich.',
        ):
            with self.subTest(wording=wording):
                job = {'title': 'Project Coordinator', 'description': wording}
                enriched = enrich_job(job)
                self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                                 {'German': 'C1', 'English': 'C1'})
                result = match_job(job, CV, enriched)
                self.assertFalse(result['eligible'])
                self.assertTrue(any('German C1' in gap for gap in result['conflicts']))

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

    def test_optional_language_list_does_not_inherit_preceding_required_fluency(self):
        job = {'title': 'Project Coordinator', 'description':
               'German fluency required and English and French A2 preferred.'}
        enriched = enrich_job(job)
        self.assertEqual({row['value']: row['level'] for row in enriched['languages']},
                         {'German': None})
        result = match_job(job, CV, enriched)
        self.assertTrue(result['eligible'])
        self.assertTrue(any('German fluency' in gap for gap in result['unknowns']))
        self.assertFalse(any('English fluency' in gap for gap in result['unknowns']))


if __name__ == '__main__':
    unittest.main()
