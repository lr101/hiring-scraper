"""Generalized regressions from private matching evaluations; no applicant data."""
import unittest

from hiring_scraper.matching import enrich_job, match_job


class StructuredApplicabilityTests(unittest.TestCase):
    def test_one_shared_tool_cannot_recommend_a_desired_title(self):
        result = match_job({'title': 'Product Owner', 'description':
            'Your responsibilities: Use Python. ' * 20},
            {'desired_roles': ['Product Owner'], 'skills': ['Python']})
        self.assertLess(result['task_fit'], 65)
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_desired_title_is_not_documented_task_fit(self):
        result = match_job({'title': 'Product Owner', 'description':
            'Your responsibilities: Evaluate customer requests and coordinate delivery schedules. ' * 10},
            {'desired_roles': ['Product Owner'], 'skills': [], 'summary': ''})
        self.assertEqual(result['task_fit'], 0)
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_academic_only_tasks_remain_a_possible_lead_without_proving_professional_fit(self):
        result = match_job({'title': 'Prototype coordinator', 'description':
            'Your tasks: Design physical prototypes. Coordinate laboratory handovers.'},
            {'cv_career_facts': {'education': [{'description':
                'Designed physical prototypes. Coordinated laboratory handovers.'}]}})
        self.assertLess(result['score'], 65)
        self.assertNotEqual(result['fit_tier'], 'recommended')
        self.assertTrue(any(r['evidence_context'] == 'academic' for r in result['task_alignment']['requirements']))

    def test_c1_satisfies_generic_fluent_english_without_an_invented_c2_gate(self):
        result = match_job({'title': 'Junior coordinator', 'description': 'Fluent English required.'},
                           {'languages': ['English'], 'language_levels': {'English': 'C1'}})
        self.assertEqual(result['gates']['language']['status'], 'pass')
        self.assertFalse(result['language_review'])

    def test_estimated_business_language_floor_requires_review_not_categorical_rejection(self):
        result = match_job({'title': 'Junior coordinator', 'description': 'Business-level fluency in German required.'},
                           {'languages': ['German'], 'language_levels': {'German': 'B2'}})
        self.assertEqual(result['gates']['language']['status'], 'unknown')
        self.assertTrue(result['language_review'])
        self.assertTrue(result['eligible'])

    def test_explicit_c1_language_shortfall_still_rejects(self):
        result = match_job({'title': 'Junior coordinator', 'description': 'German C1 required.'},
                           {'languages': ['German'], 'language_levels': {'German': 'B2'}})
        self.assertEqual(result['gates']['language']['status'], 'fail')
        self.assertFalse(result['eligible'])

    def test_german_minimum_language_and_occupational_permit_are_preserved(self):
        result = match_job({'title': 'Operator', 'description':
            'Zwingende Voraussetzungen: gültiger Staplerschein. Deutsch mindestens B2.'},
            {'language_levels': {'German': 'B1'}})
        self.assertTrue(result['mandatory_review'])
        self.assertEqual(result['gates']['language']['status'], 'fail')

    def test_bare_german_tasks_heading_retains_duty_scope(self):
        from hiring_scraper.requirements import scoped_sentences, structured_text
        clauses = list(scoped_sentences(structured_text('Aufgaben\nTermine nachhalten.')))
        self.assertIn(('Termine nachhalten.', 'duties'), clauses)

    def test_other_teams_tools_do_not_establish_specialist_fit(self):
        result = match_job({'title': 'Product Owner', 'description':
            'You own electrical control logic and commission safety interlocks. '
            'Our people team uses HubSpot for onboarding. Our design team uses Figma.'},
            {'desired_roles': ['Product Owner'], 'skills': ['HubSpot', 'Figma', 'Onboarding']})
        self.assertLess(result['score'], 30)
        self.assertTrue(any('specialist' in note.lower() for note in result['unknowns']))

    def test_structured_duties_are_not_reported_as_missing(self):
        result = match_job({'title': 'Records assistant', 'raw_metadata': {
            'responsibilities': ['Maintain inspection records.']}},
            {'summary': 'Maintained inspection records.'})
        self.assertFalse(any('responsibilities and qualification requirements are unavailable' in note
                             for note in result['unknowns']))

    def test_structured_mandatory_license_requires_review(self):
        result = match_job({'title': 'Clinical assistant', 'raw_metadata': {
            'responsibilities': ['Maintain patient records.'],
            'qualifications': {'required': ['Active professional licence required.']}}},
            {'summary': 'Maintained inspection records.'})
        self.assertTrue(result['mandatory_review'])
        self.assertEqual(result['gates']['eligibility']['status'], 'unknown')


class RequirementNegationTests(unittest.TestCase):
    def test_german_stakeholder_alignment_counts_as_coordination_evidence(self):
        result = match_job({'title': 'Operations specialist', 'description':
            'Aufgaben:\nProzessverbesserung und Stakeholder-Abstimmung. '
            'Arbeitsanweisungen aktualisieren und operative Teams unterstützen.'},
            {'skills': ['Process improvement', 'Stakeholder coordination']})
        self.assertGreaterEqual(result['content_match']['score'], 65)

    def test_degree_gate_does_not_erase_raw_task_fit(self):
        result = match_job({'title': 'Operations coordinator', 'description':
            'Your tasks:\nMaintain inspection records. Coordinate laboratory handovers.\n'
            'Requirements:\nCompleted master degree required.'},
            {'summary': 'Maintained inspection records. Coordinated laboratory handovers.'})
        self.assertGreaterEqual(result.get('task_fit', 0), 65)
        self.assertTrue(result['mandatory_review'])
        self.assertEqual(result['gates']['eligibility']['status'], 'unknown')
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_completed_cv_degree_can_satisfy_general_engineering_requirement(self):
        from hiring_scraper.requirements import education_satisfied
        requirement = {'level': 'bachelor', 'fields': ['engineering']}
        profile = {'cv_career_facts': {'education': [{'title': 'Bachelor of Engineering in Mechanical Engineering',
                    'current': False, 'end': '2020', 'description': 'Graduated with this degree.'}]}}
        self.assertTrue(education_satisfied(requirement, profile))
        profile['cv_career_facts']['education'][0]['current'] = True
        self.assertFalse(education_satisfied(requirement, profile))

    def test_ended_cv_study_needs_explicit_completion_evidence(self):
        from hiring_scraper.requirements import education_satisfied
        for description in ('Withdrew before completing the degree.', 'Studied mechanical engineering.',
                            'Awarded an academic project prize.', 'Ein Projekt erfolgreich abgeschlossen.',
                            'Did not graduate.', 'Never graduated.', 'Has not successfully completed the degree.'):
            profile = {'cv_career_facts': {'education': [{'title': 'Bachelor of Engineering in Mechanical Engineering',
                'current': False, 'end': '2020', 'description': description}]}}
            with self.subTest(description=description):
                self.assertFalse(education_satisfied({'level': 'bachelor', 'fields': ['engineering']}, profile))

    def test_german_singular_requirement_heading_restores_language_scope(self):
        result = enrich_job({'title': 'Coordinator', 'description':
            'Aufgaben\nDokumente vorbereiten.\nVoraussetzung: Deutsch B2 und Englisch C1.'})
        self.assertEqual({row['value']: row['level'] for row in result['languages']},
                         {'German': 'B2', 'English': 'C1'})

    def test_welcome_domain_is_optional_not_a_mandatory_gap(self):
        result = enrich_job({'title': 'Coordinator', 'description':
            'Voraussetzungen\nKenntnisse im Versicherungswesen sind willkommen, aber keine Voraussetzung.'})
        self.assertEqual(result['requirements'], [])

    def test_explicitly_waived_certification_is_not_a_requirement(self):
        for text in ('No industry-specific certification required.',
                     'Keine branchenspezifische Zertifizierung erforderlich.'):
            with self.subTest(text=text):
                result = enrich_job({'title': 'Support assistant',
                                     'description': 'Requirements:\n' + text})
                self.assertEqual(result['requirements'], [])

    def test_waiver_does_not_hide_separate_mandatory_certificate(self):
        result = enrich_job({'title': 'Project assistant', 'description':
                             'Requirements:\nNo industry-specific certification required. PMP required.'})
        self.assertEqual([row['value'] for row in result['requirements']], ['PMP'])


class MatchingProjectionTests(unittest.TestCase):
    def test_saved_profile_projects_matching_logistics_without_contact_details(self):
        from hiring_scraper.app.models import UserProfile
        profile = UserProfile(name='Synthetic applicant', preferences={
            'application_details': {'email': 'private@example.invalid', 'preferences': {
                'authorization_country': 'New Zealand', 'authorized_to_work': True,
                'sponsorship_required': False, 'willing_to_relocate': False}}})
        projected = profile.matching_preferences
        self.assertEqual(projected.get('application_preferences'), {
            'authorization_country': 'New Zealand', 'authorized_to_work': True,
            'sponsorship_required': False, 'willing_to_relocate': False})
        self.assertNotIn('private@example.invalid', str(projected))

    def test_explicit_cv_language_fills_missing_level_without_overwriting_saved_level(self):
        from hiring_scraper.app.models import UserProfile, ProfileCV
        text = r'\begin{document}\section*{Languages}German C1; French A2.\end{document}'
        profile = UserProfile(name='Synthetic applicant', preferences={'language_levels': {'German': 'B2'}})
        profile.cv = ProfileCV(filename='cv.tex', variants={'en': {'text': text}}, extracted_text=text)
        levels = profile.matching_preferences['language_levels']
        self.assertEqual(levels, {'German': 'B2', 'French': 'A2'})
        self.assertEqual(profile.preferences['language_levels'], {'German': 'B2'})


class ApplicabilityJudgmentTests(unittest.TestCase):
    profile = {'desired_roles': ['Product manager'],
               'skills': ['Customer service', 'Onboarding', 'Figma'],
               'summary': 'Handled customer requests and coordinated onboarding follow-up.'}
    support = ('Core work: provide operational support using Customer service, Onboarding and Figma. '
               'Maintain records, respond to requests and coordinate follow-up. ')

    def test_service_skills_are_core_when_service_is_the_actual_work(self):
        result = match_job({'title': 'Service Support Coordinator', 'description': self.support}, self.profile)
        self.assertGreaterEqual(result['score'], 65)
        self.assertEqual(result['fit_tier'], 'recommended')

    def test_unlisted_language_is_unknown_instead_of_an_invented_incompatibility(self):
        result = match_job({'title': 'Service coordinator', 'description': self.support + 'Dutch B2 required.'},
                           {**self.profile, 'languages': ['English'], 'language_levels': {'English': 'C1'}})
        self.assertTrue(result['eligible'])
        self.assertEqual(result['gates']['language']['status'], 'unknown')
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_adjacent_support_duties_surface_as_transferable_without_deployment_claim(self):
        result = match_job({'title': 'Implementation Associate', 'description':
                            'Core work: assist customers with implementation checklists. '
                            'Required: clear client communication and organized operational support. '
                            'Preferred: Customer service and Onboarding. Product-specific training is provided.'}, self.profile)
        self.assertGreaterEqual(result['score'], 30)
        self.assertLessEqual(result['score'], 64)
        self.assertTrue(any('transferable' in reason.lower() for reason in result['reasons']))

    def test_regulated_license_is_unknown_and_blocks_recommendation(self):
        result = match_job({'title': 'Clinical specialist', 'description':
                            'Core work: dispense medicines. Mandatory active pharmacist Approbation.'}, self.profile)
        self.assertEqual(result['fit_tier'], 'unlikely')
        self.assertTrue(any('Approbation' in gap for gap in result['unknowns']))

    def test_mandatory_foreign_authorization_stays_unknown_and_blocks_high_task_fit(self):
        result = match_job({'title': 'Service Support Coordinator', 'description': self.support +
                            'Mandatory: onsite in Wellington every weekday. Applicant must already hold New Zealand work authorization.',
                            'work_arrangement': 'onsite', 'locations': [{'label': 'Wellington', 'country_code': 'NZ'}]}, self.profile)
        self.assertGreaterEqual(result['qualification_fit'], 65)
        self.assertEqual(result['gates']['eligibility']['status'], 'unknown')
        self.assertEqual(result['fit_tier'], 'unlikely')
        self.assertTrue(result['eligible'])  # Unknown is not an invented incompatibility.

    def test_company_tools_do_not_count_as_applicant_matches(self):
        result = match_job({'title': 'Protection engineer', 'description':
                            'About our company: our teams use Customer service, Onboarding and Figma. '
                            'Your role: design relay protection systems. Mandatory professional protection engineering experience.'}, self.profile)
        self.assertEqual(result['matched_skills'], [])
        self.assertLess(result['score'], 30)
        self.assertTrue(any('protection' in gap.lower() for gap in result['unknowns']))

    def test_title_and_recruiting_boilerplate_do_not_supply_task_evidence(self):
        result = match_job({'title': 'Product manager', 'description':
                            'A new team is hiring. Contact recruiting for responsibilities and required qualifications. Only the title is available.'}, self.profile)
        self.assertLess(result['score'], 30)
        self.assertTrue(any('responsibilities' in item.lower() for item in result['unknowns']))

    def test_each_scoped_leadership_condition_remains_visible(self):
        result = match_job({'title': 'Director', 'description':
                            'Core work: manage department heads and approve budgets. '
                            'Mandatory: at least 12 years of professional leadership and six years managing managers.'}, self.profile)
        unknowns = ' '.join(result['unknowns']).lower()
        self.assertIn('managing managers', unknowns)
        self.assertIn('budgets', unknowns)

    def test_explicit_authorization_for_same_jurisdiction_clears_review(self):
        result = match_job({'title': 'Service Support Coordinator', 'description': self.support +
                            'Applicant must already hold New Zealand work authorization.'},
                           {**self.profile, 'application_preferences': {
                               'authorization_country': 'New Zealand', 'authorized_to_work': True,
                               'sponsorship_required': False}})
        self.assertEqual(result['gates']['eligibility']['status'], 'pass')
        self.assertEqual(result['fit_tier'], 'recommended')

    def test_authorization_elsewhere_does_not_clear_required_jurisdiction(self):
        result = match_job({'title': 'Service Support Coordinator', 'description': self.support +
                            'Applicant must already hold New Zealand work authorization.'},
                           {**self.profile, 'application_preferences': {
                               'authorization_country': 'Germany', 'authorized_to_work': True}})
        self.assertEqual(result['gates']['eligibility']['status'], 'unknown')
        self.assertEqual(result['fit_tier'], 'unlikely')

    def test_service_overlap_cannot_hide_uncovered_specialist_duties(self):
        for description in (self.support + 'Design relay protection systems and calculate fault currents.',
                            'Core work: provide operational support using Customer service, Onboarding and Figma '
                            'and design relay protection systems and calculate fault currents.'):
            result = match_job({'title': 'Operations engineer', 'description': description}, self.profile)
            self.assertLess(result['qualification_fit'], 65)
            self.assertNotEqual(result['fit_tier'], 'recommended')
            self.assertTrue(any('relay protection' in item for item in result['unknowns']))

    def test_negated_license_and_authorization_do_not_block_support_work(self):
        for waiver in ('No professional license required.', 'No work authorization required.',
                       'A professional license is not required.', 'A professional license is not mandatory.'):
            with self.subTest(waiver=waiver):
                result = match_job({'title': 'Service coordinator', 'description': self.support + waiver}, self.profile)
                self.assertFalse(result['mandatory_review'])
                self.assertEqual(result['fit_tier'], 'recommended')

    def test_current_exact_credential_can_clear_review_but_stale_or_foreign_cannot(self):
        job = {'title': 'Service coordinator', 'description': self.support +
                'Active German professional license required.'}
        for credential, expected in [('Active German professional license', 'pass'),
                                     ('Expired German professional license', 'unknown'),
                                     ('German professional license, no longer valid', 'unknown'),
                                     ('Active French professional license', 'unknown')]:
            with self.subTest(credential=credential):
                result = match_job(job, {**self.profile, 'certifications': [credential]})
                self.assertEqual(result['gates']['eligibility']['status'], expected)
                self.assertEqual(result['mandatory_review'], expected != 'pass')

    def test_eligibility_conditions_aggregate_independently_of_order(self):
        conditions = ['Active professional license required.',
                      'Applicant must hold New Zealand work authorization.']
        profile = {**self.profile, 'application_preferences': {
            'authorization_country': 'New Zealand', 'authorized_to_work': True, 'sponsorship_required': False}}
        for rows in (conditions, list(reversed(conditions))):
            result = match_job({'title': 'Service coordinator',
                                'description': self.support + ' '.join(rows)}, profile)
            self.assertEqual(result['gates']['eligibility']['status'], 'unknown')
            self.assertTrue(result['mandatory_review'])

    def test_explicit_denial_in_required_jurisdiction_is_a_failed_gate(self):
        result = match_job({'title': 'Service coordinator', 'description': self.support +
                            'Applicant must hold New Zealand work authorization.'},
                           {**self.profile, 'application_preferences': {
                               'authorization_country': 'New Zealand', 'authorized_to_work': False}})
        self.assertFalse(result['eligible'])
        self.assertEqual(result['gates']['eligibility']['status'], 'fail')

    def test_compound_eligibility_obligations_require_every_condition(self):
        parts = ['Active German professional license required', 'active French professional license required']
        profile = {**self.profile, 'certifications': ['Active German professional license']}
        for conditions in (parts, list(reversed(parts))):
            result = match_job({'title': 'Service coordinator',
                                'description': self.support + ' and '.join(conditions) + '.'}, profile)
            self.assertEqual(result['gates']['eligibility']['status'], 'unknown')
            self.assertTrue(result['mandatory_review'])
        result = match_job({'title': 'Service coordinator', 'description': self.support +
                            'Applicant must hold New Zealand work authorization and Australian work authorization.'},
                           {**self.profile, 'application_preferences': {
                               'authorization_country': 'New Zealand', 'authorized_to_work': True,
                               'sponsorship_required': False}})
        self.assertEqual(result['gates']['eligibility']['status'], 'unknown')

    def test_unclassified_context_is_not_professional_or_academic_proof(self):
        result = match_job({'title': 'Methods assistant', 'description':
                            'Core work: support supervised demonstrations using PowerPoint. '
                            'Required: academic project exposure to PowerPoint; professional production experience is not required.'},
                           {'skills': ['PowerPoint'], 'skill_evidence': {'PowerPoint': {
                               'context': 'Historical CV; retain original work or study context',
                               'note': 'Prepared office presentations.'}}})
        self.assertEqual(result['profile_skill_context']['PowerPoint']['context'], 'unspecified')
        academic = [row for row in result['requirement_observations'] if row.get('kind') == 'academic']
        self.assertEqual(academic[0]['status'], 'unknown')
        self.assertEqual(academic[0]['evidence_source'], 'profile.skill_evidence.PowerPoint.note')
        self.assertGreaterEqual(result['score'], 30)
        self.assertLessEqual(result['score'], 64)

    def test_scoped_tenure_rows_preserve_both_word_and_digit_durations(self):
        result = match_job({'title': 'Director', 'description':
                            'Core work: own operations strategy, manage department heads and approve budgets. '
                            'Mandatory: at least 12 years of relevant professional leadership and six years managing managers. '
                            'Preferred tools: Customer service and Onboarding.'},
                           {**self.profile, 'experience_years': 5})
        rows = [row for row in result['requirement_observations'] if row.get('kind') == 'experience']
        self.assertEqual([(row['minimum_years'], row['status']) for row in rows], [(12, 'missing'), (6, 'missing')])
        self.assertTrue(all(row['evidence_source'] == 'profile.experience_years' for row in rows))
        self.assertFalse(any('Preferred tools' in row['requirement'] for row in result['requirement_observations']))

    def test_total_tenure_cannot_verify_managerial_tenure(self):
        result = match_job({'title': 'Director', 'description':
                            'Mandatory: five years managing managers.'},
                           {**self.profile, 'experience_years': 15})
        row = next(row for row in result['requirement_observations'] if row.get('kind') == 'experience')
        self.assertEqual(row['status'], 'unknown')
        self.assertEqual(row['scope'], 'managing managers')


class StructuredRequirementModalityTests(unittest.TestCase):
    def test_mandatory_heading_scopes_unmarked_degree_bullet(self):
        result = enrich_job({'title': 'Research associate', 'description':
            '<h2>Zwingend</h2><ul><li>Doctorate in chemistry</li></ul>'})
        self.assertTrue(any(row['kind'] == 'education' and row['level'] == 'doctorate'
                            for row in result['requirements']))

    def test_structured_evidence_does_not_include_generated_headings(self):
        job = {'title': 'Coordinator', 'raw_metadata': {
            'qualifications': {'required': ['Python'], 'preferred': ['French C1']}}}
        result = enrich_job(job)
        self.assertEqual(next(row['evidence'] for row in result['skills']
                              if row['name'] == 'Python'), 'Python')

    def test_plain_structured_qualification_list_retains_requirement_scope(self):
        result = enrich_job({'title': 'Coordinator', 'raw_metadata': {
            'qualifications': ['Engineering bachelor degree', 'Python']}})
        self.assertTrue(any(row['kind'] == 'education' for row in result['requirements']))
        self.assertTrue(any(row['value'] == 'Python' for row in result['requirements']))

    def test_nested_required_and_preferred_fields_keep_separate_modality(self):
        job = {'title': 'Coordinator', 'description': '', 'raw_metadata': {
            'qualifications': {'required': ['German C1', 'Engineering bachelor degree'],
                               'preferred': ['French C1', 'Python']}}}
        result = enrich_job(job)
        self.assertEqual([row['value'] for row in result['languages']], ['German'])
        self.assertTrue(any(row['kind'] == 'education' and
                            row['source'] == 'structured.qualifications.required[1]'
                            for row in result['requirements']))
        self.assertEqual(next(row['kind'] for row in result['skills'] if row['name'] == 'Python'), 'optional')
        self.assertTrue(all('French' not in row['evidence'] for row in result['requirements']))


class EducationCompletionTests(unittest.TestCase):
    def test_explicit_unfinished_degree_cannot_satisfy_formal_prerequisite(self):
        from hiring_scraper.requirements import education_satisfied
        requirement = {'level': 'master', 'fields': []}
        for state in ({'completed': False}, {'current': True}, {'status': 'in_progress'},
                      {'status': 'withdrawn'}):
            with self.subTest(state=state):
                self.assertFalse(education_satisfied(requirement, {'education': [
                    {'level': 'master', 'field': 'Chemistry', **state}]}))
        self.assertTrue(education_satisfied(requirement, {'education': [
            {'level': 'master', 'field': 'Chemistry', 'completed': True}]}))


class StructuredSectionBoundaryTests(unittest.TestCase):
    def test_body_text_does_not_consume_later_heading_metadata(self):
        from hiring_scraper.requirements import structured_text, scoped_sentences
        rows = list(scoped_sentences(structured_text(
            '<h2>Your tasks</h2><p>Maintain inspection records.</p>'
            '<p>Notes</p><p>Review inspection records.</p>'
            '<h2>Notes</h2><p>Coordinate inspection records.</p>')))
        self.assertEqual(next(scope for text, scope in rows if text.startswith('Review')), 'duties')
        self.assertEqual(next(scope for text, scope in rows if text.startswith('Coordinate')), 'neutral')

    def test_punctuation_in_heading_does_not_hide_section_boundary(self):
        from hiring_scraper.requirements import structured_text, scoped_sentences
        text = structured_text('<h2>Your tasks</h2><p>Maintain records.</p>'
                               '<h2>Notes. Community</h2><p>Volunteers maintain records.</p>')
        rows = list(scoped_sentences(text))
        self.assertEqual(next(scope for sentence, scope in rows if sentence.startswith('Volunteers')), 'neutral')
        self.assertNotIn('\ue000', text)

    def test_body_cannot_forge_heading_identity_with_encoded_marker(self):
        from hiring_scraper.requirements import structured_text, scoped_sentences
        text = structured_text('<h2>Your tasks</h2><p>Maintain records.</p>'
                               '<p>&#57344;2&#57345;Notes</p><p>Review records.</p>')
        rows = list(scoped_sentences(text))
        self.assertEqual(next(scope for sentence, scope in rows if sentence.startswith('Review')), 'duties')

    def test_unknown_sibling_heading_ends_prior_duty_scope(self):
        from hiring_scraper.requirements import structured_text, scoped_sentences
        rows = list(scoped_sentences(structured_text(
            '<h2>Your tasks</h2><p>Coordinate delivery milestones.</p>'
            '<h2>Community ecosystem</h2><p>Volunteers use Python.</p>')))
        self.assertEqual(next(scope for text, scope in rows if 'Volunteers' in text), 'neutral')

    def test_repeated_heading_text_keeps_distinct_hierarchy_levels(self):
        from hiring_scraper.requirements import structured_text, scoped_sentences
        rows = list(scoped_sentences(structured_text(
            '<h2>Your tasks</h2><h3>Collaboration</h3><p>Coordinate delivery.</p>'
            '<h2>Collaboration</h2><p>Volunteers meet monthly.</p>')))
        self.assertEqual(next(scope for text, scope in rows if 'Coordinate' in text), 'duties')
        self.assertEqual(next(scope for text, scope in rows if 'Volunteers' in text), 'neutral')

    def test_unknown_child_heading_retains_parent_duty_scope(self):
        from hiring_scraper.requirements import structured_text, scoped_sentences
        rows = list(scoped_sentences(structured_text(
            '<h2>Your tasks</h2><h3>Delivery collaboration</h3>'
            '<p>Coordinate delivery milestones.</p>')))
        self.assertEqual(next(scope for text, scope in rows if 'Coordinate' in text), 'duties')


class CVFactAlignmentTests(unittest.TestCase):
    def test_cv_record_evidence_reaches_local_matching_with_work_study_scope(self):
        from hiring_scraper.matching import profile_content_sources, _profile_content_skills
        profile = {'cv_career_facts': {
            'experiences': [{'description': 'Built Python services.'}],
            'education': [{'description': 'Built Java services.'}]}}
        sources = profile_content_sources(profile)
        skills = {name: (weight, source) for name, weight, source in _profile_content_skills(sources)}
        self.assertEqual(skills.get('Python', (None,))[0], 1.0)
        self.assertEqual(skills.get('Java', (None,))[0], 0.5)
        self.assertIn('education[0]', skills['Java'][1])


class WorkplaceEvidenceTests(unittest.TestCase):
    def test_default_false_remote_flag_without_arrangement_remains_unknown_matching_input(self):
        from hiring_scraper.app.enrichment import job_input
        from hiring_scraper.app.models import Job
        job = Job(title='Synthetic role', is_remote=False)
        self.assertIsNone(job_input(job)['is_remote'])
        job.work_arrangement = 'onsite'
        self.assertIs(job_input(job)['is_remote'], False)
        job.work_arrangement = None
        job.is_remote = True
        self.assertIs(job_input(job)['is_remote'], True)
