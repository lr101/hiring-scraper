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
