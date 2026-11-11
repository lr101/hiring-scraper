"""Requirement claims must be tied to factual source excerpts."""
import unittest

import importlib
import importlib.util


class RequirementEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.requirement_evidence'),
                             'Shared grounded evidence assessment is missing')
        global evidence
        evidence = importlib.import_module('hiring_scraper.app.requirement_evidence')
        self.sources = {'job.description': 'Python required. Leadership preferred.',
                        'profile.summary': 'Built Python services.',
                        'profile.career_goals': 'Lead teams.'}
        self.row = {'evidence_distance': 'adjacent', 'category': 'capability', 'requirement': 'Python', 'priority': 'required', 'importance': 'core', 'status': 'supported',
                    'job_source': 'job.description', 'job_quote': 'Python required.',
                    'evidence_source': 'profile.summary', 'evidence_quote': 'Built Python services.',
                    'explanation': 'Direct service experience.'}

    def test_incidental_tool_overlap_cannot_compensate_for_unknown_core_work(self):
        rows = [{**self.row, 'status': 'unknown'},
                *[{**self.row, 'importance': 'supporting', 'requirement': f'Tool {i}'} for i in range(20)]]
        self.assertEqual(evidence.role_relevance_score(rows), 15)
        rows[0]['status'] = 'transferable'
        self.assertEqual(evidence.role_relevance_score(rows), 58)
        rows[0]['status'] = 'supported'
        self.assertEqual(evidence.role_relevance_score(rows), 100)

    def test_one_unverified_duty_does_not_erase_strong_documented_core_overlap(self):
        rows = [{**self.row, 'requirement': f'Core duty {i}'} for i in range(9)]
        rows.append({**self.row, 'requirement': 'One additional duty', 'status': 'unknown'})
        self.assertEqual(evidence.role_relevance_score(rows), 90)

    def test_adjacent_core_work_still_cannot_become_a_strong_match_from_tools(self):
        rows = [{**self.row, 'status': 'transferable'},
                *[{**self.row, 'importance': 'supporting'} for _ in range(10)]]
        self.assertLess(evidence.role_relevance_score(rows), 65)

    def test_close_transfer_receives_substantial_credit_without_clearing_the_requirement(self):
        row = {**self.row, 'status': 'transferable', 'evidence_distance': 'close'}
        self.assertEqual(evidence.role_relevance_score([row]), 80)
        summary = evidence.requirement_summary([row])
        self.assertEqual(summary['coverage'], 80)

    def test_splitting_one_unknown_employer_clause_does_not_multiply_its_penalty(self):
        rows = [self.row, *[{**self.row, 'requirement': f'Part {i}',
            'job_quote': 'Coordinate campaigns, tests, reports and events.', 'status': 'unknown'} for i in range(4)]]
        self.assertEqual(evidence.role_relevance_score(rows), 50)

    def test_splitting_one_supported_employer_clause_does_not_inflate_its_credit(self):
        rows = [*[{**self.row, 'requirement': f'Part {i}'} for i in range(4)],
                {**self.row, 'job_quote': 'Own regulatory approval.', 'status': 'unknown'}]
        self.assertEqual(evidence.role_relevance_score(rows), 50)

    def test_sparse_job_can_have_no_requirements_without_fabrication(self):
        self.assertEqual(evidence.ground_requirements([], self.sources), [])
        self.assertEqual(evidence.role_relevance_score([]), 0)

    def test_unknown_logistics_do_not_reduce_supported_task_qualification_score(self):
        rows = [self.row, {**self.row, 'requirement': 'Work authorization',
                          'priority': 'logistics', 'importance': 'supporting', 'status': 'unknown'}]
        self.assertEqual(evidence.role_relevance_score(rows), 100)

    def test_mandatory_language_is_separate_from_task_fit_even_when_core(self):
        rows = [self.row, {**self.row, 'requirement': 'French C1',
                          'category': 'language', 'status': 'missing'}]
        self.assertEqual(evidence.role_relevance_score(rows), 100)

    def test_in_progress_education_cannot_ground_supported_completed_degree(self):
        row = {**self.row, 'category': 'education', 'requirement': 'Completed master degree',
               'job_quote': 'Completed master degree required.',
               'evidence_source': 'profile.cv_career_facts.education[0].title',
               'evidence_quote': 'Master of Chemistry'}
        sources = {'job.description': row['job_quote'],
                   row['evidence_source']: row['evidence_quote'],
                   'profile.cv_career_facts.education[0].current': 'True'}
        result = evidence.ground_requirements([row], sources)[0]
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['evidence_quote'], '')

    def test_unfinished_degree_cannot_support_plain_degree_requirement(self):
        for status in ('in_progress', 'in-progress', 'in progress', 'incomplete'):
            with self.subTest(status=status):
                row = {**self.row, 'category': 'education', 'requirement': 'Bachelor degree',
                       'job_quote': 'Bachelor degree required.',
                       'evidence_source': 'profile.education[0].degree', 'evidence_quote': 'Bachelor'}
                sources = {'job.description': row['job_quote'],
                           row['evidence_source']: 'Bachelor', 'profile.education[0].status': status}
                self.assertEqual(evidence.ground_requirements([row], sources)[0]['status'], 'unknown')

    def test_current_enrollment_can_support_explicit_enrollment_requirement(self):
        row = {**self.row, 'category': 'education', 'requirement': 'Enrolled in Bachelor program',
               'job_quote': 'Must be currently enrolled in a Bachelor program.',
               'evidence_source': 'profile.education[0].degree', 'evidence_quote': 'Bachelor'}
        sources = {'job.description': row['job_quote'], row['evidence_source']: 'Bachelor',
                   'profile.education[0].status': 'in-progress'}
        self.assertEqual(evidence.ground_requirements([row], sources)[0]['status'], 'supported')

    def test_unverifiable_support_becomes_unknown_without_fabricated_evidence(self):
        row = {**self.row, 'evidence_quote': 'Managed fifty engineers.'}
        result = evidence.ground_requirements([row], self.sources)[0]
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['evidence_quote'], '')
        self.assertIn('unverified', result['explanation'].lower())

    def test_job_requirement_must_actually_be_in_named_job_source(self):
        with self.assertRaises(ValueError):
            evidence.ground_requirements([{**self.row, 'job_quote': 'Kubernetes required.'}], self.sources)

    def test_aspirations_and_writing_samples_are_not_factual_sources(self):
        sources = evidence.evidence_sources({'summary': 'Built Python services.',
            'career_goals': 'Lead teams.', 'work_preferences': 'Remote only.',
            'desired_roles': ['Director'], 'application_personalization': {'writing_samples': 'Led 50 people.'}},
            {'description': 'Python required.'})
        self.assertEqual(sources, {'profile.summary': 'Built Python services.', 'job.description': 'Python required.'})

    def test_blank_job_quotes_are_rejected(self):
        with self.assertRaises(ValueError):
            evidence.ground_requirements([{**self.row, 'job_quote': '   '}], self.sources)

    def test_large_sources_are_bounded_without_losing_late_requirements(self):
        sources = evidence.evidence_sources({'work_history': [
            {'description': 'A' * 20000} for _ in range(100)]},
            {'description': 'About us. ' * 10000 + 'German C1 required.'})
        self.assertLess(sum(map(len, sources.values())), 25000)
        self.assertIn('German C1 required.', sources['job.description'])

    def test_source_specific_verified_evidence_is_preserved(self):
        result = evidence.ground_requirements([self.row], self.sources)
        self.assertEqual(result, [self.row])
        self.assertEqual(evidence.requirement_summary(result),
                         {'coverage': 100, 'completeness': 100, 'required_gaps': []})

    def test_country_agnostic_sponsorship_flag_cannot_prove_foreign_authorization_failure(self):
        row = {**self.row, 'requirement': 'Hold New Zealand work authorization',
               'priority': 'logistics', 'status': 'missing', 'job_quote': 'New Zealand work authorization required.',
               'evidence_source': 'profile.application_preferences.sponsorship_required', 'evidence_quote': 'True'}
        sources = {'job.description': row['job_quote'], row['evidence_source']: 'True'}
        checked = evidence.ground_requirements([row], sources)[0]
        self.assertEqual(checked['status'], 'unknown')

    def test_authorization_sentence_does_not_invalidate_separate_relocation_row(self):
        row = {**self.row, 'category': 'location', 'requirement': 'Relocate for onsite work',
               'priority': 'logistics', 'status': 'transferable',
               'job_quote': 'Onsite work requires relocation. New Zealand work authorization required.',
               'evidence_source': 'profile.application_preferences.willing_to_relocate',
               'evidence_quote': 'True'}
        sources = {'job.description': row['job_quote'], row['evidence_source']: 'True'}
        self.assertEqual(evidence.ground_requirements([row], sources)[0]['status'], 'transferable')

    def test_explicit_denial_only_applies_to_the_named_authorization_jurisdiction(self):
        row = {**self.row, 'requirement': 'Hold New Zealand work authorization',
               'priority': 'logistics', 'status': 'missing', 'job_quote': 'New Zealand work authorization required.',
               'evidence_source': 'profile.application_preferences.authorized_to_work', 'evidence_quote': 'False'}
        for country, status in [('New Zealand', 'missing'), ('Germany', 'unknown')]:
            sources = {'job.description': row['job_quote'], row['evidence_source']: 'False',
                       'profile.application_preferences.authorization_country': country}
            self.assertEqual(evidence.ground_requirements([row], sources)[0]['status'], status)

    def test_broad_skill_inventory_does_not_starve_career_and_gate_evidence(self):
        profile = {
            'skills': [f'Tool {i}' for i in range(100)],
            'skill_evidence': {f'Tool {i}': {'context': 'professional', 'note': 'Used for records. ' * 100}
                               for i in range(100)},
            'work_history': [{'title': 'Assistant', 'description': 'Maintained customer records.'}],
            'cv_career_facts': {'education': [{'description': 'University prototype project.'}]},
            'career_evidence': 'Coordinated service follow-up.',
            'language_levels': {'German': 'B2'},
            'education': [{'level': 'bachelor', 'field': 'Design'}],
            'application_preferences': {'willing_to_relocate': False},
        }
        sources = evidence.evidence_sources(profile, {'description': 'Customer records required.'})
        for key, quote in {
            'profile.work_history[0].description': 'Maintained customer records.',
            'profile.cv_career_facts.education[0].description': 'University prototype project.',
            'profile.career_evidence': 'Coordinated service follow-up.',
            'profile.language_levels.German': 'B2',
            'profile.education[0].field': 'Design',
            'profile.application_preferences.willing_to_relocate': 'False',
        }.items():
            with self.subTest(source=key):
                self.assertIn(quote, sources.get(key, ''))
        self.assertLess(sum(map(len, sources.values())), 25000)

    def test_job_logistics_and_structured_requirements_have_citable_sources(self):
        sources = evidence.evidence_sources({}, {
            'description': 'About the company. ' * 2000,
            'location_text': 'Onsite in Tokyo; no remote work.',
            'raw_metadata': {'qualifications': 'A current professional license is mandatory.'},
        })
        self.assertEqual(sources.get('job.location_text'), 'Onsite in Tokyo; no remote work.')
        self.assertEqual(sources.get('job.raw_metadata.qualifications'),
                         'A current professional license is mandatory.')

    def test_deep_untrusted_metadata_does_not_break_other_citable_requirements(self):
        nested = 'Unrelated metadata'
        for _ in range(2000):
            nested = {'nested': nested}
        sources = evidence.evidence_sources({}, {'raw_metadata': {
            'unrelated': nested, 'qualifications': 'Active license required.'}})
        self.assertEqual(sources['job.raw_metadata.qualifications'], 'Active license required.')

    def test_typical_cv_bullets_and_skill_notes_are_not_truncated_by_short_fields(self):
        note = 'Performed intake, maintained records, and resolved requests. ' * 5
        profile = {'skill_evidence': {f'Tool {i}': {'context': 'mixed; verify original scope', 'note': note}
                                      for i in range(10)},
                   'cv_career_facts': {'experiences': [{'title': 'Assistant', 'bullets': [note]}
                                                     for _ in range(10)]}}
        sources = evidence.evidence_sources(profile, {})
        self.assertEqual(sources['profile.skill_evidence.Tool 5.note'], note)
        self.assertEqual(sources['profile.cv_career_facts.experiences[5].bullets[0]'], note)

    def test_unknowns_are_visible_and_preferred_skills_do_not_hide_required_gaps(self):
        rows = [self.row, {**self.row, 'requirement': 'Leadership', 'priority': 'preferred', 'status': 'unknown'},
                {**self.row, 'requirement': 'Authorization', 'priority': 'logistics', 'status': 'missing'}]
        self.assertEqual(evidence.requirement_summary(rows),
                         {'coverage': 50, 'completeness': 89, 'required_gaps': ['Authorization']})
