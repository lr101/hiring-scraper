"""Content matches must survive unfamiliar titles without promoting boilerplate."""
import unittest

from hiring_scraper.matching import enrich_job, match_job


PROFILE = {'desired_roles': ['Software developer'], 'skills': ['Python', 'SQL', 'Docker']}
DUTIES = ('Your responsibilities:\nBuild services using Python and SQL. '
          'Deploy services using Docker and maintain reliable customer integrations. '
          'Work with the team to design, deliver and document changes to our systems. ' * 3)


class ContentMatchingTests(unittest.TestCase):
    def test_unfamiliar_title_with_matching_work_is_recommended(self):
        result = match_job({'title': 'Solutions Specialist', 'description': DUTIES}, PROFILE)
        self.assertEqual(result['fit_tier'], 'recommended')
        self.assertGreaterEqual(result['score'], 65)
        self.assertTrue(any('content' in reason.lower() for reason in result['reasons']))

    def test_content_outranks_preferred_title_with_unrelated_work(self):
        relevant = match_job({'title': 'Solutions Specialist', 'description': DUTIES}, PROFILE)
        title = match_job({'title': 'Software developer', 'description':
                           'Your responsibilities: Prepare marketing campaigns and sales proposals. ' * 8}, PROFILE)
        self.assertGreater(relevant['score'], title['score'])

    def test_profile_summary_and_work_descriptions_supply_matching_evidence(self):
        for content in [
            {'summary': 'Built Python services with SQL and Docker.'},
            {'application_details': {'experiences': [{'description': 'Built Python services with SQL and Docker.'}]}},
            {'application_personalization': {'career_evidence': 'Built Python services with SQL and Docker.'}},
        ]:
            with self.subTest(content=content):
                result = match_job({'title': 'Solutions Specialist', 'description': DUTIES},
                                   {'desired_roles': ['Software developer'], **content})
                self.assertEqual(result['fit_tier'], 'recommended')
                self.assertTrue(any('profile' in reason.lower() for reason in result['reasons']))
                self.assertEqual(set(result['matched_skills']), {'Python', 'SQL', 'Docker'})
                self.assertEqual(result['missing_skills'], [])

    def test_german_tasks_match_english_profile_capabilities_with_no_title_overlap(self):
        result = match_job({'title': 'Referent Organisation', 'description':
                            'Ihre Aufgaben:\nSie koordinieren Projekte, erheben Anforderungen und optimieren Prozesse. '
                            'Sie begleiten die Umsetzung und dokumentieren die Ergebnisse für alle Beteiligten. ' * 4},
                           {'desired_roles': ['Project coordinator'],
                            'summary': 'Project management, requirements gathering and process improvement.'})
        self.assertEqual(result['fit_tier'], 'recommended')
        self.assertEqual(set(result['matched_skills']),
                         {'Project management', 'Requirements gathering', 'Process improvement'})

    def test_requirements_as_work_output_do_not_become_applicant_qualifications(self):
        for task in ['Gather requirements and coordinate projects.',
                     'Sie erheben Anforderungen und koordinieren Projekte.']:
            with self.subTest(task=task):
                enriched = enrich_job({'title': 'Specialist', 'description': 'Your tasks:\n' + task})
                self.assertEqual(enriched['requirements'], [])

    def test_explicit_mandatory_cues_in_duties_remain_requirements(self):
        enriched = enrich_job({'title': 'Specialist', 'description':
                               'Your tasks:\nGather requirements. Python skills required.'})
        self.assertTrue(any(row['value'] == 'Python' for row in enriched['requirements']))

    def test_requirement_labels_inside_duties_retain_mandatory_checks(self):
        for label, requirement in [('Requirement:', 'Python skills'),
                                   ('Requirement:', 'PMP certification'),
                                   ('Requirement:', 'German C1'),
                                   ('Requirements:', 'German C1'),
                                   ('Anforderungen:', 'Deutsch C1')]:
            with self.subTest(requirement=requirement):
                enriched = enrich_job({'title': 'Specialist', 'description':
                                       f'Your tasks:\n{label} {requirement}'})
                if 'C1' in requirement:
                    self.assertEqual(enriched['languages'][0]['value'], 'German')
                else:
                    self.assertTrue(enriched['requirements'])

    def test_secondary_role_can_be_recommended_when_content_proves_a_stronger_fit(self):
        result = match_job({'title': 'Solutions Specialist', 'description': DUTIES},
                           {**PROFILE, 'secondary_roles': ['Solutions Specialist']})
        self.assertEqual(result['fit_tier'], 'recommended')

    def test_title_only_skill_mentions_do_not_supply_content_evidence(self):
        result = match_job({'title': 'Python SQL Docker Specialist', 'description':
                            'Your tasks: Organize office supplies and greet visitors. ' * 10}, PROFILE)
        self.assertLess(result['score'], 30)

    def test_benefits_optional_skills_and_generic_tools_are_not_strong_content(self):
        for description in [
            'Benefits:\nPython, SQL and Docker training. ' * 20,
            'Preferred qualifications:\nPython, SQL and Docker. ' * 20,
            'Your responsibilities:\nCustomer service, onboarding, Excel and Git. ' * 20,
        ]:
            with self.subTest(description=description[:50]):
                result = match_job({'title': 'Office Specialist', 'description': description},
                                   {**PROFILE, 'skills': PROFILE['skills'] + ['Customer service', 'Onboarding', 'Excel', 'Git']})
                self.assertLess(result['score'], 30)

    def test_repeated_single_tool_does_not_make_strong_content(self):
        result = match_job({'title': 'Specialist', 'description': 'Your tasks: Python. ' * 60}, PROFILE)
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_overlapping_coordination_aliases_count_as_one_competency(self):
        result = match_job({'title': 'Operations Specialist', 'description':
                            'Your responsibilities: Project coordination. ' * 20},
                           {'desired_roles': ['Project manager'],
                            'summary': 'Experienced in project coordination.'})
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_narrative_negations_and_aspirations_are_not_capabilities(self):
        for summary in ['No experience with Python, SQL or Docker.',
                        'Want to learn Python, SQL and Docker.',
                        'Keine Erfahrung mit Python, SQL oder Docker.']:
            with self.subTest(summary=summary):
                result = match_job({'title': 'Specialist', 'description': DUTIES},
                                   {'desired_roles': ['Software developer'], 'summary': summary})
                self.assertLess(result['score'], 30)

    def test_content_does_not_bypass_mandatory_qualification_or_language_gaps(self):
        for requirement in ['Requirements:\nPMP certification required.',
                            'Requirements:\nGerman C1 required.']:
            with self.subTest(requirement=requirement):
                result = match_job({'title': 'Specialist', 'description': DUTIES + '\n' + requirement},
                                   {**PROFILE, 'languages': ['German'], 'language_levels': {'German': 'B1'}})
                self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_narrative_capabilities_do_not_verify_mandatory_professional_experience(self):
        result = match_job({'title': 'Specialist', 'description': DUTIES +
                           '\nRequirements:\nProfessional Python experience required.'},
                          {'desired_roles': ['Software developer'],
                           'summary': 'Used Python, SQL and Docker in academic projects.'})
        self.assertNotEqual(result['fit_tier'], 'recommended')
        self.assertTrue(result['requirement_gaps'])


if __name__ == '__main__':
    unittest.main()
