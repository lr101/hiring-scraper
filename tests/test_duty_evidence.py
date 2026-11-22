import unittest

from hiring_scraper.matching import match_job


class DutyEvidenceTests(unittest.TestCase):
    def test_requirements_paraphrase_matches_work_at_the_same_scope(self):
        job = {'description': 'Ihre Aufgaben:\nSie übersetzen fachliche Anforderungen in Produktspezifikationen.'}
        profile = {'application_details': {'experiences': [{'description':
            'Translated business requirements into product specifications.'}]}}
        result = match_job(job, profile)['duty_evidence']
        self.assertEqual(result['duties'][0]['status'], 'direct')
        self.assertTrue(result['duties'][0]['evidence_quote'])

    def test_repeated_tools_do_not_establish_scope_or_add_duties(self):
        job = {'description': 'Your tasks:\nOwn the commercial product roadmap.\n' + 'Figma and Excel.\n' * 30}
        profile = {'skills': ['Figma', 'Excel'], 'summary': 'Created Figma prototypes.'}
        result = match_job(job, profile)['duty_evidence']
        self.assertEqual(len(result['duties']), 1)
        self.assertEqual(result['duties'][0]['status'], 'unverified')

    def test_academic_work_does_not_satisfy_independent_professional_delivery(self):
        job = {'description': 'Your tasks:\nOwn hardware program delivery.'}
        profile = {'duty_evidence': [{'quote': 'Coordinated hardware program delivery.',
                                    'context': 'academic', 'responsibility': 'coordinated'}]}
        result = match_job(job, profile)['duty_evidence']
        self.assertNotEqual(result['duties'][0]['status'], 'direct')

    def test_explicit_assistance_does_not_prove_independent_coordination(self):
        job = {'description': 'Your tasks:\nCoordinate project delivery.'}
        profile = {'duty_evidence': [{'quote': 'Coordinated project delivery.',
                                    'context': 'professional', 'responsibility': 'assisted'}]}
        result = match_job(job, profile)['duty_evidence']
        self.assertEqual(result['duties'][0]['status'], 'transferable')

    def test_specialist_domain_limits_generic_process_overlap(self):
        job = {'description': 'Your tasks:\nImprove financial accounting processes.'}
        profile = {'summary': 'Improved customer service processes.'}
        self.assertLess(match_job(job, profile)['duty_evidence']['score'], 50)

    def test_plain_feed_duty_heading_excludes_employer_and_qualifications(self):
        job = {'description': 'Wir entwickeln technische Produkte.\n'
               'Das erwartet Dich bei uns Terminkoordination: Du koordinierst Termine und organisierst Besprechungen.\n'
               'Das solltest Du mitbringen Erfahrung in der Abrechnung.\n'
               'Das bieten wir Dir Weiterbildung.'}
        result = match_job(job, {'summary': 'Coordinated schedules and organized meetings.'})['duty_evidence']
        self.assertTrue(result['duties'])
        self.assertTrue(all('Termine' in row['job_quote'] or 'Besprechungen' in row['job_quote']
                            for row in result['duties']))

    def test_documented_duties_drive_rank_with_multiple_distinct_examples(self):
        job = {'title': 'Project coordinator', 'description':
               'Your tasks:\nMaintain issue registers.\nCreate status presentations.\n'
               'Translate business requirements into product specifications.'}
        profile = {'summary': 'Maintained issue registers. Created status presentations. '
                              'Translated business requirements into product specifications.'}
        result = match_job(job, profile)
        self.assertEqual(result['static_rank_basis'], 'duties')
        self.assertEqual(result['task_fit'], result['duty_evidence']['score'])
