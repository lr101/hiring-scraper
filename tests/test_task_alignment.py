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

    def test_assistance_scope_governs_later_conjoined_profile_actions(self):
        for fact in ('Assisted with creating slide decks and maintaining schedules.',
                     'Supported creating slide decks and maintaining schedules.'):
            with self.subTest(fact=fact):
                row = self.align('Independently maintain schedules.', [('experience.0', fact)])['requirements'][0]
                self.assertEqual(row['status'], 'transferable')
                self.assertEqual(row['evidence_quote'], fact)

    def test_independent_job_scope_governs_every_conjoined_duty(self):
        for fact in ('Created schedules and maintained action registers in a university project.',
                     'Created schedules and maintained action registers under supervision.'):
            with self.subTest(fact=fact):
                result = self.align('Independently create schedules and maintain action registers.',
                                    [('summary', fact)])
                self.assertEqual([row['status'] for row in result['requirements']],
                                 ['transferable', 'transferable'])

    def test_explicit_actor_switch_is_not_applicant_evidence(self):
        for fact in ('Maintained schedules and my manager created slide decks.',
                     'Maintained schedules and a senior colleague approved slide decks.',
                     'Pflegte Terminpläne und meine Kollegin erstellte Präsentationen.'):
            with self.subTest(fact=fact):
                result = self.align('Maintain schedules. Create slide decks.', [('experience.0', fact)])
                self.assertEqual([row['status'] for row in result['requirements']], ['supported', 'unknown'])

    def test_planning_work_products_offer_adjacent_maintenance_evidence(self):
        result = self.align('Maintain the schedule and project charter.', [('summary',
            'Project planning: created a project charter and work breakdown structure; '
            'scheduled milestones using a Gantt chart')])
        self.assertEqual(result['requirements'][0]['status'], 'transferable')

    def test_documented_procedures_offer_adjacent_maintenance_evidence(self):
        result = self.align('Pflege von Arbeitsanweisungen.',
                            [('experience.0', 'Documented standard operating procedures.')])
        self.assertEqual(result['requirements'][0]['status'], 'transferable')

    def test_generic_action_families_enter_source_backed_evidence(self):
        for duty, fact in (
            ('Plan project milestones.', 'Planned project milestones.'),
            ('Prepare training materials.', 'Prepared training materials.'),
            ('Track action items.', 'Tracked action items.'),
            ('Schedule workshops.', 'Scheduled workshops.'),
            ('Research customer needs.', 'Researched customer needs.'),
            ('Maintain schedules.', 'Maintained detailed schedules.')):
            with self.subTest(duty=duty):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['requirements'][0]['status'],
                                 'supported')

    def test_incidental_modifier_does_not_hide_specific_deliverable(self):
        row = self.align('Maintain detailed schedules.',
                         [('experience.0', 'Maintained schedules.')])['requirements'][0]
        self.assertEqual(row['status'], 'supported')

    def test_work_product_equivalence_does_not_establish_domain_scope(self):
        row = self.align('Maintain clinical schedules.',
                         [('experience.0', 'Maintained sales schedules.')])['requirements'][0]
        self.assertNotEqual(row['status'], 'supported')

    def test_senior_second_actor_does_not_change_applicant_autonomy_scope(self):
        row = self.align('Maintain schedules and a senior colleague approves budgets.',
                         [('cv_career_facts.education[0].description', 'Maintained schedules.')])['requirements'][0]
        self.assertEqual(row['status'], 'supported')

    def test_other_actor_scope_continues_until_applicant_or_sentence_reset(self):
        for fact in (
            'Maintained schedules and my manager created slide decks and documented SOPs.',
            'Maintained schedules and a colleague created slide decks and documented SOPs.',
            'Pflegte Terminpläne und eine Kollegin erstellte Präsentationen und dokumentierte Arbeitsanweisungen.'):
            with self.subTest(fact=fact):
                rows = self.align('Maintain schedules. Create slide decks. Document SOPs.',
                                  [('experience.0', fact)])['requirements']
                self.assertEqual([row['status'] for row in rows], ['supported', 'unknown', 'unknown'])
        for fact in (
            'My manager created slide decks and I documented SOPs.',
            'My manager created slide decks. Documented SOPs.',
            'Meine Kollegin erstellte Präsentationen und ich dokumentierte Arbeitsanweisungen.'):
            with self.subTest(fact=fact):
                row = self.align('Document SOPs.', [('experience.0', fact)])['requirements'][0]
                self.assertEqual(row['status'], 'supported')
                self.assertIn(row['evidence_quote'], fact)

    def test_document_contents_and_research_interests_assert_no_work(self):
        for duty, fact in (
            ('Maintain schedules.', 'The annual plan contains schedules.'),
            ('Plan milestones.', 'The annual plan contains milestones.'),
            ('Research customer needs.', 'Research interests: customer needs.'),
            ('Research customer needs.', 'Interested in researching customer needs.'),
            ('Plan milestones.', 'Interested in planning milestones.'),
            ('Maintain schedules.', 'The planning document describes schedules.')):
            with self.subTest(fact=fact):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['score'], 0)

    def test_bare_plan_and_research_document_nouns_are_not_actions(self):
        for duty, fact in (
            ('Plan milestones.', 'My plan for milestones.'),
            ('Research customer needs.', 'My research report lists customer needs.'),
            ('Research customer needs.', 'Research on customer needs.')):
            with self.subTest(fact=fact):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['score'], 0)

    def test_document_subject_does_not_turn_gerund_into_profile_action(self):
        self.assertEqual(self.align('Plan milestones.',
            [('experience.0', 'My planning document lists milestones.')])['score'], 0)
        self.assertEqual(self.align('Research customer needs.',
            [('experience.0', 'The research report describes customer needs.')])['score'], 0)

    def test_topic_and_note_subjects_are_not_performed_work(self):
        for duty, fact in (('Research customer needs.', 'Research topics: customer needs.'),
                           ('Plan milestones.', 'My planning notes cover milestones.')):
            with self.subTest(fact=fact):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['score'], 0)

    def test_non_work_clause_does_not_erase_explicit_performed_work(self):
        for duty, fact in (
            ('Document SOPs.', 'Documented SOPs and my research interests include customer needs.'),
            ('Maintain schedules.', 'The annual plan contains milestones and I maintained schedules.'),
            ('Maintain schedules.', 'My planning notes cover milestones and I maintained schedules.')):
            with self.subTest(fact=fact):
                row = self.align(duty, [('experience.0', fact)])['requirements'][0]
                self.assertEqual(row['status'], 'supported')
                self.assertIn(row['evidence_quote'], fact)

    def test_declared_note_context_bounds_authority_without_changing_source(self):
        from hiring_scraper.task_alignment import align_tasks
        for context in ('academic', 'research', 'professional'):
            with self.subTest(context=context):
                source, quote = 'skill_evidence.task.note', 'Maintained schedules.'
                row = align_tasks({'description': 'Independently maintain schedules.'},
                    [(source, quote)], source_contexts={source: context})['requirements'][0]
                self.assertEqual(row['status'], 'supported' if context == 'professional' else 'transferable')
                self.assertEqual(row['evidence_context'], context)
                self.assertEqual(row['evidence_source'], source)
                self.assertEqual(row['evidence_quote'], quote)
        row = align_tasks({'description': 'Own schedules.'},
            [('skill_evidence.task.note', 'Maintained schedules.')],
            source_contexts={'skill_evidence.task.note': 'professional'})['requirements'][0]
        self.assertNotEqual(row['status'], 'supported')

    def test_profile_plan_research_require_predicates_beyond_surface_nouns(self):
        for duty, fact in (
            ('Research customer needs.', 'Research questions: customer needs.'),
            ('Plan milestones.', 'My planning notebook concerns milestones.')):
            with self.subTest(fact=fact):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['score'], 0)
        for duty, fact in (
            ('Plan milestones.', 'During the internship, I planned milestones.'),
            ('Research customer needs.', 'I researched customer needs.'),
            ('Research customer needs.', 'Researching customer needs.')):
            with self.subTest(fact=fact):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['score'], 100)

    def test_personal_interest_clause_preserves_sibling_work_and_actor_reset(self):
        for fact in ('Documented SOPs and I am interested in researching customer needs.',
                     'I am interested in researching customer needs and I documented SOPs.'):
            with self.subTest(fact=fact):
                self.assertEqual(self.align('Document SOPs.', [('experience.0', fact)])['score'], 100)
        result = self.align('Research customer needs.', [('experience.0',
            'My manager maintained schedules and I am researching customer needs.')])
        self.assertEqual(result['score'], 100)

    def test_interest_scope_cannot_turn_coordinated_intents_into_work(self):
        fact = 'Interested in creating presentations and maintaining schedules.'
        self.assertEqual(self.align('Maintain schedules.', [('summary', fact)])['score'], 0)
        fact = 'Documented SOPs and interested in researching customer needs.'
        self.assertEqual(self.align('Document SOPs.', [('summary', fact)])['score'], 100)

    def test_explicit_predicates_keep_adverbs_and_note_object_colons(self):
        for duty, fact in (
            ('Plan milestones.', 'I independently planned milestones.'),
            ('Research customer needs.', 'I actively researched customer needs.'),
            ('Document SOPs.', 'I documented reports: SOPs.'),
            ('Maintain schedules and milestones.', 'Maintained notes: schedules and milestones.')):
            with self.subTest(fact=fact):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['score'], 100)

    def test_personal_possession_of_notes_is_not_performed_work(self):
        for duty, fact in (
            ('Plan milestones.', 'I have notes: planning milestones.'),
            ('Research customer needs.', 'I have notes: researching customer needs.')):
            with self.subTest(fact=fact):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['score'], 0)

    def test_posting_language_and_attendance_statements_are_not_duties(self):
        from hiring_scraper.task_alignment import task_units
        for text in (
            'This job description documents the selection criteria.',
            'Applications are reviewed by the recruitment office.',
            'You are scheduled to work in the office on Tuesdays.',
            'Documentation is maintained in fluent Danish.',
            'Die Dokumentation erfolgt in fließendem Dänisch.',
        ):
            with self.subTest(text=text):
                self.assertEqual(task_units({'description': text}), [])
        self.assertEqual(len(task_units({'description': 'Review customer applications.'})), 1)
        self.assertEqual(len(task_units({'description': 'Maintain Danish training documentation.'})), 1)

    def test_routine_performed_actions_have_bilingual_task_evidence(self):
        for duty, fact in (
            ('Record assembly observations.', 'Recorded assembly observations.'),
            ('Erfassen Sie Montagebeobachtungen.', 'Erfasste Montagebeobachtungen.'),
            ('Collect supplier feedback.', 'Collected supplier feedback.'),
            ('Sammeln Sie Lieferantenrückmeldungen.', 'Sammelte Lieferantenrückmeldungen.'),
            ('Draw process maps.', 'Zeichnete Prozesslandkarten.'),
            ('Compare inspection results.', 'Compared inspection results.'),
            ('Vergleichen Sie Prüfergebnisse.', 'Verglich Prüfergebnisse.'),
            ('Summarize interview findings.', 'Summarized interview findings.'),
            ('Fassen Sie Befragungsergebnisse zusammen.', 'Fasste Befragungsergebnisse zusammen.'),
            ('Present progress slides.', 'Präsentierte Fortschrittsfolien.'),
        ):
            with self.subTest(duty=duty):
                rows = self.align(duty, [('experience.0', fact)])['requirements']
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['status'], 'supported')
                self.assertEqual(rows[0]['evidence_quote'], fact)

    def test_specific_work_products_match_nominal_duties_to_performed_work(self):
        for duty, fact in (
            ('Erstellung von Ist-Prozesslandkarten.', 'Created as-is process maps.'),
            ('Erstellung von Soll-Prozesslandkarten.', 'Created to-be process maps.'),
            ('Pflege von Arbeitsanweisungen.', 'Maintained work instructions.'),
            ('Pflege von Problemlisten.', 'Maintained issue logs.'),
            ('Erstellung von RACI-Matrizen.', 'Created RACI matrices.'),
            ('Erstellung von Projektstrukturplänen.', 'Created a WBS.'),
        ):
            with self.subTest(duty=duty):
                self.assertEqual(self.align(duty, [('experience.0', fact)])['requirements'][0]['status'],
                                 'supported')

    def test_nominal_profile_inventory_does_not_prove_performed_work(self):
        for fact in ('Documentation: work instructions.', 'Creation of process maps.',
                     'Records: assembly observations.', 'Presentation: progress slides.'):
            with self.subTest(fact=fact):
                self.assertEqual(self.align('Document work instructions. Create process maps. '
                    'Record assembly observations. Present progress slides.', [('summary', fact)])['score'], 0)

    def test_one_shared_generic_object_does_not_establish_domain_transfer(self):
        self.assertEqual(self.align('Maintain hydraulic equipment.',
            [('experience.0', 'Maintained kitchen equipment.')])['score'], 0)

    def test_process_map_creation_does_not_prove_engineering_authority(self):
        row = self.align('Independently own process maps.', [('summary',
            'Drew process maps in a university project.')])['requirements'][0]
        self.assertNotEqual(row['status'], 'supported')


    def test_language_medium_filter_keeps_concrete_document_storage_work(self):
        from hiring_scraper.task_alignment import task_units
        self.assertEqual(len(task_units({'description':
            'Documentation is maintained in a shared archive.'})), 1)
        self.assertEqual(task_units({'description':
            'Documentation is maintained in Portuguese.'}), [])
        self.assertEqual(task_units({'description':
            'We review your application after submission.'}), [])

    def test_drawing_a_map_is_creation_evidence_with_bounded_authority(self):
        result = self.align('Create process maps. Independently own process maps.',
            [('cv_career_facts.education[0].description', 'Drew process maps.')])
        self.assertEqual([row['status'] for row in result['requirements']],
                         ['supported', 'unknown'])
        self.assertTrue(all(row['evidence_context'] == 'academic' for row in result['requirements']))

    def test_map_phase_does_not_disappear_during_deliverable_canonicalization(self):
        self.assertNotEqual(self.align('Create to-be process maps.',
            [('experience.0', 'Created as-is process maps.')])['requirements'][0]['status'], 'supported')

    def test_passive_application_work_is_distinct_from_recruiting_metadata(self):
        from hiring_scraper.task_alignment import task_units
        self.assertEqual(len(task_units({'description':
            'Applications are developed for hospital logistics.'})), 1)
        self.assertEqual(len(task_units({'description':
            'Applications are reviewed for customers.'})), 1)

    def test_german_action_nouns_alone_are_not_performed_work(self):
        for duty, fact in [('Erfassen Sie Montagebeobachtungen.', 'Erfassung von Montagebeobachtungen.'),
                           ('Create process maps.', 'Zeichnung von Prozesslandkarten.')]:
            with self.subTest(fact=fact):
                self.assertEqual(self.align(duty, [('summary', fact)])['score'], 0)

    def test_posting_creation_and_cefr_statements_are_not_duties(self):
        from hiring_scraper.task_alignment import task_units
        for text in ('A fictional job advertisement was created for recruitment testing.',
                     'German B2 is expected for occasional documentation.'):
            with self.subTest(text=text):
                self.assertEqual(task_units({'description': text}), [])

    def test_record_duties_allow_modal_and_adverb_predicates(self):
        for duty in ('You will record assembly observations.', 'You regularly record assembly observations.'):
            with self.subTest(duty=duty):
                rows = self.align(duty, [('experience.0', 'Recorded assembly observations.')])['requirements']
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['status'], 'supported')
