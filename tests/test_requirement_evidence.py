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
        self.row = {'requirement': 'Python', 'priority': 'required', 'importance': 'core', 'status': 'supported',
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

    def test_sparse_job_can_have_no_requirements_without_fabrication(self):
        self.assertEqual(evidence.ground_requirements([], self.sources), [])
        self.assertEqual(evidence.role_relevance_score([]), 0)

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

    def test_unknowns_are_visible_and_preferred_skills_do_not_hide_required_gaps(self):
        rows = [self.row, {**self.row, 'requirement': 'Leadership', 'priority': 'preferred', 'status': 'unknown'},
                {**self.row, 'requirement': 'Authorization', 'priority': 'logistics', 'status': 'missing'}]
        self.assertEqual(evidence.requirement_summary(rows),
                         {'coverage': 50, 'completeness': 89, 'required_gaps': ['Authorization']})
