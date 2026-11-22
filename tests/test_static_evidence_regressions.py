"""Frozen synthetic judgments: tools cannot substitute for uncovered core duties.

Positive means recommendation warranted by documented duties, not a hiring label.
Initial scenarios were fixed before the matcher change; a separate final
holdout below was evaluated without tuning against it.
"""
import unittest

from hiring_scraper.matching import match_job

PROFILE = {
    'desired_roles': ['Product manager', 'Project manager'],
    'skills': ['Figma', 'Excel', 'Project management', 'Requirements gathering',
               'Stakeholder coordination', 'Process improvement'],
    'summary': 'Created Figma prototypes. Documented requirements. '
               'Coordinated stakeholder workshops. Maintained issue registers. '
               'Created status presentations. Improved onboarding processes.',
    'language_levels': {'German': 'B2', 'English': 'C1'},
}
TOOLS = 'Figma, Excel, project management, requirements gathering, stakeholder coordination.'
# Initial development scenarios; labels follow actual duty evidence.
CASES = [
    ('dev-coordination', 'Document requirements.\nCoordinate stakeholder workshops.\nCreate Figma prototypes.', True),
    ('dev-commercial', 'Own commercial product strategy.\nApprove pricing decisions.\n' + TOOLS, False),
    ('dev-partial', 'Document requirements.\nCoordinate stakeholder workshops.\nOwn commercial product strategy.\nApprove pricing decisions.\n' + TOOLS, False),
    ('holdout-reporting', 'Maintain issue registers.\nCreate status presentations.', True),
    ('holdout-regulatory', 'Approve pharmaceutical release decisions.\nOwn clinical safety assessments.\n' + TOOLS, False),
    ('holdout-domain', 'Create turbine prototypes.\nDocument clinical requirements.\n' + TOOLS, False),
]


def posting(duties, suffix=''):
    return {'title': 'Product manager', 'description': 'Your tasks:\n' + duties + '\n' + suffix}


class StaticEvidenceRegressions(unittest.TestCase):
    def test_frozen_duty_judgments(self):
        for name, duties, positive in CASES:
            with self.subTest(name=name):
                result = match_job(posting(duties), PROFILE)
                if positive:
                    self.assertEqual(result['fit_tier'], 'recommended')
                else:
                    self.assertLess(result['task_fit'], 65)
                    self.assertLess(result['score'], 65)
                    self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_boilerplate_cannot_upgrade_uncovered_core_duties(self):
        for name, duties, positive in CASES:
            with self.subTest(name=name):
                result = match_job(posting(duties, '\nAbout us:\n' +
                    'We operate internationally and value collaboration across our offices. ' * 12), PROFILE)
                self.assertEqual(result['fit_tier'] == 'recommended', positive)

    def test_supporting_duties_do_not_prove_independent_ownership(self):
        duties = CASES[0][1] + '\nMaintain issue registers. Create status presentations.\nOwn commercial product strategy.'
        result = match_job(posting(duties), PROFILE)
        self.assertLess(result['task_fit'], 65)
        self.assertLess(result['score'], 65)

    def test_shared_tool_does_not_clear_authority_gap(self):
        result = match_job(posting(CASES[0][1] + '\nIndependently own Figma product prototypes.'), PROFILE)
        self.assertLess(result['score'], 65)
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_research_tasks_do_not_establish_professional_scope(self):
        result = match_job(posting('Maintain issue registers. Create status presentations.'),
            {'skill_evidence': {'Documentation': {'context': 'research',
                'note': 'Maintained issue registers. Created status presentations.'}}})
        self.assertLess(result['score'], 65)
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_unrelated_concrete_duties_cannot_hide_behind_tool_inventory(self):
        for duties in ('Document regulatory submissions. Prepare audit reports.',
                       'Maintain financial ledgers. Prepare statutory tax returns.'):
            result = match_job(posting(duties + '\n' + TOOLS, '\nAbout us:\n' + 'We value collaboration. ' * 30), PROFILE)
            self.assertLess(result['score'], 65)
            self.assertLess(result['task_fit'], 65)

    def test_company_wide_work_remains_applicant_evidence(self):
        result = match_job(posting('Maintain issue registers. Create status presentations.'),
            {'summary': 'Company-wide, I maintained issue registers. Organisation-wide, I created status presentations.'})
        self.assertEqual(result['fit_tier'], 'recommended')

    def test_research_tool_note_keeps_its_declared_context(self):
        result = match_job(posting('Build Python services. Maintain SQL databases. Deploy Docker containers.'),
            {'skill_evidence': {'Python': {'context': 'research',
                'note': 'Built Python services with SQL and Docker.'}}})
        self.assertLess(result['content_match']['score'], 65)
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_keyword_stuffing_does_not_clear_uncovered_work(self):
        result = match_job(posting(CASES[1][1] + '\n' + TOOLS * 30), PROFILE)
        self.assertLess(result['task_fit'], 65)
        self.assertLess(result['score'], 65)
        self.assertTrue(any('dut' in note.lower() for note in result['unknowns']))

    def test_course_does_not_upgrade_attained_language(self):
        result = match_job(posting(CASES[0][1], 'Requirements:\nGerman C1 required.'),
                           {**PROFILE, 'summary': PROFILE['summary'] + ' Attending German C1 course.'})
        self.assertEqual(result['gates']['language']['status'], 'fail')
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_credential_review_preserves_actual_task_evidence(self):
        result = match_job(posting(CASES[0][1], 'Requirements:\nActive professional licence required.'), PROFILE)
        self.assertGreaterEqual(result['task_fit'], 65)
        self.assertEqual(result['gates']['eligibility']['status'], 'unknown')

    def test_employer_tasks_are_not_applicant_work(self):
        result = match_job(posting('Maintain issue registers. Create status presentations.'),
                           {'summary': 'Our company maintained issue registers. Our company created status presentations.'})
        self.assertEqual(result['duty_evidence']['score'], 0)
        self.assertNotEqual(result['fit_tier'], 'recommended')

    def test_employer_narrative_is_not_applicant_capability(self):
        result = match_job({'title': 'Specialist', 'description':
                           'Your tasks:\nPython, SQL and Docker.'},
                          {'summary': 'Our company builds Python, SQL and Docker products.'})
        self.assertEqual(result['content_match']['score'], 0)

# Final holdout: frozen after implementation, evaluated without further tuning.
# These are separate requirements and evidence combinations, not historical
# applications or previous model judgments used as positive labels.
HOLDOUT = [
    ('records-and-requirements', 'Document requirements. Maintain issue registers.', {}, '', True),
    ('presentations-and-workshops', 'Create status presentations. Coordinate stakeholder workshops.', {}, '', True),
    ('unfamiliar-title', CASES[0][1], {'desired_roles': []}, '', True),
    ('explicit-b2', CASES[0][1], {}, 'German B2 required.', True),
    ('optional-c1', CASES[0][1], {}, 'German C1 is optional.', True),
    ('required-c1', CASES[0][1], {}, 'German C1 required.', False),
    ('missing-language', CASES[0][1], {'language_levels': {}}, 'German C1 required.', False),
    ('masters-required', CASES[0][1], {'education': [{'degree': 'bachelor', 'completed': True}]}, 'Completed master degree required.', False),
    ('licensed-work', CASES[0][1], {}, 'Active medical license required.', False),
    ('academic-only', 'Create status presentations. Maintain issue registers.',
     {'summary': '', 'skills': [], 'cv_career_facts': {'education': [{'description': 'Created status presentations. Maintained issue registers.'}]}}, '', False),
    ('scoped-seniority', CASES[0][1], {'experience_years': 4}, 'At least 8 years of professional product leadership required.', False),
    ('onsite-refused', CASES[0][1], {'application_preferences': {'willing_to_work_onsite': False}}, 'On-site attendance required.', False),
    ('missing-context', '', {}, '', False),
    ('negated-profile', CASES[0][1], {'summary': 'Never created Figma prototypes or documented requirements. Want to coordinate stakeholder workshops.', 'skills': []}, '', False),
]


class FinalHoldoutTests(unittest.TestCase):
    def test_frozen_recommendation_judgments(self):
        for name, duties, changes, requirement, positive in HOLDOUT:
            with self.subTest(name=name):
                job = posting(duties, 'Requirements:\n' + requirement if requirement else '')
                if name == 'onsite-refused':
                    job['work_arrangement'] = 'onsite'
                result = match_job(job, {**PROFILE, **changes})
                self.assertEqual(result['fit_tier'] == 'recommended', positive)
