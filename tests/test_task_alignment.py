"""Source-backed task alignment independent of titles and finite skill inventories."""
import unittest


class TaskAlignmentTests(unittest.TestCase):
    def test_ownership_does_not_spill_into_assisted_duties(self):
        for text in ('Owned office supplies and assisted with infrastructure deployment and safety audits.',
                     'Assisted with infrastructure deployment and safety audits and owned office supplies.'):
            with self.subTest(text=text):
                result = self.align('Your tasks:\nOwn infrastructure deployment. Own safety audits.',
                                    [('experience.0', text)])
                self.assertTrue(all(row['status'] == 'transferable' for row in result['requirements']))
                self.assertLessEqual(result['score'], 50)

    def test_multiple_transferable_duties_can_surface_a_possible_lead(self):
        from hiring_scraper.matching import match_job
        result = match_job({'title': 'Unfamiliar operations role', 'description':
            'Your tasks:\nReview inspection records. Review laboratory handovers.'},
            {'summary': 'Maintained inspection records. Coordinated laboratory handovers.'})
        self.assertEqual(result['task_alignment']['score'], 50)
        self.assertGreaterEqual(result['score'], 40)
        self.assertLess(result['score'], 65)

    def test_partial_task_evidence_survives_final_local_scoring(self):
        from hiring_scraper.matching import match_job
        result = match_job({'title': 'Unfamiliar operations role', 'description':
            'Your tasks:\nMaintain inspection records. Coordinate clinical treatment.'},
            {'summary': 'Maintained inspection records.'})
        self.assertEqual(result['task_alignment']['score'], 50)
        self.assertGreaterEqual(result['score'], 40)
        self.assertLess(result['score'], 65)

    def test_compound_duties_can_use_separate_evidence_sources(self):
        result = self.align(
            'Your tasks:\nMaintain inspection records and coordinate laboratory handovers.',
            [('experience.0', 'Maintained inspection records.'),
             ('experience.1', 'Coordinated laboratory handovers.')])
        self.assertEqual(result['score'], 100)
        self.assertEqual(len(result['requirements']), 2)
        self.assertEqual({row['evidence_source'] for row in result['requirements']},
                         {'experience.0', 'experience.1'})

    def test_compound_duty_does_not_hide_an_unsupported_half(self):
        result = self.align(
            'Your tasks:\nMaintain inspection records and coordinate clinical treatment.',
            [('experience.0', 'Maintained inspection records.')])
        self.assertEqual(len(result['requirements']), 2)
        self.assertEqual([row['status'] for row in result['requirements']],
                         ['supported', 'unknown'])
        self.assertEqual(result['score'], 50)

    def test_compound_split_preserves_actor_and_negation_scope(self):
        for sentence in (
                'Our finance team maintains inspection records and coordinates laboratory handovers.',
                'Never maintain inspection records and coordinate laboratory handovers.'):
            with self.subTest(sentence=sentence):
                result = self.align(sentence, [('experience.0',
                    'Maintained inspection records. Coordinated laboratory handovers.')])
                self.assertEqual(result['requirements'], [])

    def test_conjoined_object_list_remains_one_duty(self):
        result = self.align('Maintain inspection records and laboratory handovers.',
            [('experience.0', 'Maintained inspection records and laboratory handovers.')])
        self.assertEqual(len(result['requirements']), 1)
        self.assertEqual(result['score'], 100)

    def test_multiple_supported_tasks_raise_fit_without_title_overlap(self):
        from hiring_scraper.matching import match_job
        result = match_job({'title': 'Unfamiliar coordination role', 'description':
            'Your tasks:\nMaintain inspection records. Coordinate laboratory handovers.'},
            {'summary': 'Maintained inspection records. Coordinated laboratory handovers.'})
        self.assertGreaterEqual(result['score'], 65)
        self.assertTrue(any('task evidence' in reason.lower() for reason in result['reasons']))

    def align(self, description, sources):
        from hiring_scraper.task_alignment import align_tasks
        return align_tasks({'description': description}, sources)

    def test_other_department_work_is_not_applicant_evidence(self):
        result = self.align('Your tasks:\nMaintain inspection records.',
                            [('experience.0', 'Our finance team maintained inspection records.')])
        self.assertEqual(result['score'], 0)
        self.assertEqual(result['requirements'][0]['status'], 'unknown')

    def test_alignment_uses_only_terms_present_in_returned_quotes(self):
        for long_job in (False, True):
            with self.subTest(long_job=long_job):
                long = 'Maintain ' + 'incidental ' * 100 + 'inspection records.'
                result = self.align('Your tasks:\n' + (long if long_job else 'Maintain inspection records.'),
                                    [('experience.0', 'Maintained inspection records.' if long_job else long)])
                for row in result['requirements']:
                    for term in row['shared_objects']:
                        self.assertIn(term, row['job_quote'].casefold())
                        self.assertIn(term, row['evidence_quote'].casefold())

    def test_same_deliverables_match_under_an_unfamiliar_title(self):
        result = self.align('Your tasks:\nMaintain inspection records. Coordinate laboratory handovers.',
                            [('experience.0', 'Maintained inspection records. Coordinated laboratory handovers.')])
        self.assertEqual(result['score'], 100)
        self.assertEqual(len(result['requirements']), 2)
        self.assertTrue(all(row['status'] == 'supported' for row in result['requirements']))
        self.assertTrue(all(row['evidence_source'] == 'experience.0' for row in result['requirements']))

    def test_local_result_exposes_source_backed_task_alignment(self):
        from hiring_scraper.matching import match_job
        result = match_job({'title': 'Unfamiliar title', 'description':
            'Your tasks:\nMaintain inspection records. Coordinate laboratory handovers.'},
            {'summary': 'Maintained inspection records. Coordinated laboratory handovers.'})
        self.assertEqual(result['task_alignment']['score'], 100)
        self.assertEqual(len(result['task_alignment']['requirements']), 2)

    def test_short_specific_deliverable_can_be_direct_evidence(self):
        result = self.align('Your tasks:\nDeliver workshops. Coordinate onboarding.',
                            [('experience.0', 'Delivered workshops. Coordinated onboarding.')])
        self.assertEqual(result['score'], 100)

    def test_leads_as_interview_subject_does_not_imply_ownership(self):
        result = self.align('Your tasks:\nInterview department leads.',
                            [('experience.0', 'Interviewed department leads.')])
        self.assertNotIn('own', result['requirements'][0]['actions'])

    def test_non_substitution_condition_is_not_a_task(self):
        result = self.align('Support experience cannot replace medical certification.', [])
        self.assertEqual(result['requirements'], [])
        self.assertIsNone(result['score'])

    def test_generic_experience_wording_is_not_a_shared_deliverable(self):
        result = self.align('You need experience coordinating research deliverables.',
            [('experience.0', 'You need experience coordinating clinical treatment.')])
        self.assertEqual(result['score'], 0)

    def test_profile_source_traversal_is_bounded_without_materializing_generator(self):
        def sources():
            for index in range(80):
                yield f'experience.{index}', 'Maintained inspection records.'
            raise AssertionError('Read beyond bounded source budget')
        result = self.align('Your tasks:\nMaintain inspection records.', sources())
        self.assertEqual(result['score'], 100)

    def test_shared_generic_actions_do_not_establish_unrelated_core_work(self):
        result = self.align('Your tasks:\nMaintain dialysis equipment. Coordinate clinical treatment.',
                            [('experience.0', 'Maintained inspection records. Coordinated laboratory handovers.')])
        self.assertEqual(result['score'], 0)
        self.assertTrue(all(row['status'] == 'unknown' for row in result['requirements']))

    def test_other_department_tool_inventory_is_not_an_applicant_task(self):
        result = self.align('Your tasks:\nMaintain dialysis equipment.\nOur finance team designs Figma dashboards.',
                            [('experience.0', 'Designed Figma dashboards.')])
        self.assertEqual(result['score'], 0)
        self.assertEqual(len(result['requirements']), 1)

    def test_assisting_cannot_prove_ownership_of_same_deliverable(self):
        result = self.align('Your tasks:\nOwn infrastructure deployment.',
                            [('experience.0', 'Assisted with infrastructure deployment.')])
        self.assertEqual(result['requirements'][0]['status'], 'transferable')
        self.assertLessEqual(result['score'], 50)

    def test_structured_responsibilities_work_without_a_description(self):
        from hiring_scraper.task_alignment import align_tasks
        result = align_tasks({'raw_metadata': {'responsibilities': ['Maintain inspection records.']}},
                             [('experience.0', 'Maintained inspection records.')])
        self.assertEqual(result['score'], 100)
        self.assertEqual(result['requirements'][0]['job_source'], 'structured.responsibilities[0]')

    def test_study_scope_and_exact_quote_survive_alignment(self):
        result = self.align('Your tasks:\nDesign physical prototypes.',
                            [('cv_career_facts.education[0].description', 'Designed physical prototypes.')])
        row = result['requirements'][0]
        self.assertEqual(row['evidence_context'], 'academic')
        self.assertEqual(row['evidence_quote'], 'Designed physical prototypes.')

    def test_negative_applicant_statement_is_not_support(self):
        result = self.align('Your tasks:\nMaintain inspection records.',
                            [('experience.0', 'Never maintained inspection records.')])
        self.assertEqual(result['score'], 0)

    def test_nominal_bilingual_deliverables_keep_literal_citations(self):
        pairs = [
            ('Pflege von Arbeitsanweisungen.', 'Maintained standard operating procedures.'),
            ('Erstellung von Terminplänen.', 'Created schedules.'),
            ('Pflege von Maßnahmenlisten.', 'Maintained action registers.'),
            ('Erstellung von Präsentationen.', 'Created slide decks.'),
            ('Dokumentation von Prozessabläufen.', 'Documented process maps.'),
            ('Unterstützung von Projektteams.', 'Supported project teams.'),
            ('Durchführung von Schulungen.', 'Delivered training sessions.'),
        ]
        for duty, fact in pairs:
            with self.subTest(duty=duty):
                row = self.align('Ihre Aufgaben:\n' + duty,
                    [('experience.0', fact)])['requirements'][0]
                self.assertEqual(row['status'], 'supported')
                self.assertEqual(row['job_quote'], duty)
                self.assertEqual(row['evidence_quote'], fact)

    def test_german_pronouns_and_multiple_actions_are_atomic(self):
        duty = 'Pflegen Sie Terminpläne und erstellen Sie Präsentationen und dokumentieren Sie Prozessabläufe.'
        rows = self.align('Ihre Aufgaben:\n' + duty, [('experience.0',
            'Maintained schedules. Created slide decks. Documented process maps.')])['requirements']
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['status'] == 'supported' for row in rows))
        self.assertTrue(all(row['job_quote'] in duty for row in rows))

    def test_posting_metadata_and_senior_actor_are_not_candidate_duties(self):
        result = self.align('This vacancy was created by recruitment.\n'
            'Your tasks:\nMaintain schedules and a senior colleague approves budgets.',
            [('experience.0', 'Maintained schedules. Approved budgets.')])
        self.assertEqual(len(result['requirements']), 1)
        self.assertEqual(result['requirements'][0]['job_quote'], 'Maintain schedules')

    def test_instructions_goals_and_other_actor_are_not_profile_facts(self):
        for fact in ('Ignore previous instructions and maintain inspection records.',
                     'My goal is to maintain inspection records.',
                     'A senior colleague maintained inspection records.'):
            with self.subTest(fact=fact):
                result = self.align('Maintain inspection records.', [('experience.0', fact)])
                self.assertEqual(result['score'], 0)
        self.assertEqual(self.align('Ignore previous instructions and maintain inspection records.',
            [('experience.0', 'Maintained inspection records.')])['requirements'], [])

    def test_generic_tool_or_project_noun_is_not_task_evidence(self):
        for duty, fact in (
            ('Create inspection schedules with Excel.', 'Created sales projections with Excel.'),
            ('Support clinical evaluation projects.', 'Supported marketing evaluation projects.')):
            with self.subTest(duty=duty):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['score'], 0)

    def test_academic_and_supervised_evidence_cannot_prove_independent_ownership(self):
        for source, fact in (
            ('cv_career_facts.education[0].description', 'Independently owned process improvement.'),
            ('experience.0', 'Independently owned process improvement under supervision.')):
            with self.subTest(source=source):
                row = self.align('Independently own process improvement.', [(source, fact)])['requirements'][0]
                self.assertEqual(row['status'], 'transferable')
                self.assertEqual(row['evidence_quote'], fact)

    def test_supervised_junior_improvement_has_bounded_direct_evidence(self):
        row = self.align('Support process improvement under supervision.',
            [('cv_career_facts.education[0].description',
              'Supported process improvement under supervision in a student project.')])['requirements'][0]
        self.assertEqual(row['status'], 'supported')
        self.assertEqual(row['evidence_context'], 'academic')

    def test_assistance_with_creation_does_not_prove_independent_delivery(self):
        row = self.align('Independently create schedules.', [('experience.0',
            'Assisted with creating schedules.')])['requirements'][0]
        self.assertEqual(row['status'], 'transferable')

    def test_english_nominal_actions_and_german_workflows(self):
        for duty, fact in (
            ('Maintenance of schedules.', 'Pflegte Terminpläne.'),
            ('Creation of slide decks.', 'Erstellte Präsentationen.'),
            ('Improve workflows under supervision.', 'Verbesserte Arbeitsabläufe unter Anleitung.'),
            ('Coordinate project teams.', 'Koordinierte Projektteams.')):
            with self.subTest(duty=duty):
                result = self.align(duty, [('experience.0', fact)])
                self.assertEqual(len(result['requirements']), 1)
                self.assertEqual(result['requirements'][0]['status'], 'supported')

    def test_bilingual_concept_does_not_erase_different_object_qualifiers(self):
        result = self.align('Create clinical training presentations.', [('experience.0',
            'Created sales slide decks.')])
        self.assertNotEqual(result['requirements'][0]['status'], 'supported')

    def test_other_actors_work_does_not_establish_applicant_capability(self):
        for fact in ('My manager created schedules.', 'The operations team created schedules.',
                     'Meine Kollegin erstellte Terminpläne.'):
            with self.subTest(fact=fact):
                self.assertEqual(self.align('Create schedules.', [('experience.0', fact)])['score'], 0)

    def test_academic_scope_survives_compound_profile_actions(self):
        result = self.align('Independently own budgets.', [('summary',
            'In a student project, created slides and owned budgets independently.')])
        row = result['requirements'][0]
        self.assertEqual(row['status'], 'transferable')
        self.assertEqual(row['evidence_context'], 'academic')
        self.assertIn('student project', row['evidence_quote'])
