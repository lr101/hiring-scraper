"""Matcher v3: reranker-checked evidence, exact ledger, pending jobs stay hidden."""
import unittest
from types import SimpleNamespace

from hiring_scraper.matching_v3 import match_job_v3
from hiring_scraper.matching_v3 import weights as W
from hiring_scraper.matching_v3.evidence import (
    CANDIDATES_PER_ITEM, MAX_SCORED_ITEMS, compute_evidence, is_soft_requirement, rerank_query, scored_items,
    units_source,
)
from hiring_scraper.matching_v3.score import components, seniority_penalty, support, title_level

PROFILE = {
    'desired_roles': ['Customer Success Manager'],
    'skills': ['HubSpot', 'Trello'],
    'summary': 'Customer success lead in SaaS.',
    'cv_career_facts': {'experiences': [{
        'title': 'Customer Success Manager',
        'description': 'HubSpot (KPI-Monitoring, Dashboards, Support-Tickets, Schulungen) for SaaS customers.'}]},
    'language_levels': {'German': 'C2', 'English': 'C1'},
    'work_styles': ['onsite', 'hybrid'],
}
JOB = {
    'title': 'Technischer Sachbearbeiter Oberflächentechnik (m/w/d)',
    'description': 'Durchführung von Schulungen. Technische Ausbildung im Korrosionsschutz. Gute Englischkenntnisse.',
    'location_text': 'Karlsruhe',
}
# What the LLM extracts from the screenshot posting: benefits are not items.
ANALYSIS = {
    'lines': [], 'occupation': 'surface engineering / corrosion protection', 'industry': 'automotive fasteners',
    'duties': [{'line': 1, 'text': 'Durchführung von Schulungen'},
               {'line': 1, 'text': 'Entwicklung Oberflächenschlüssel für Verbindungselemente'}],
    'requirements': [
        {'line': 2, 'text': 'Technische Ausbildung im Korrosionsschutz', 'need': 'must', 'kind': 'education'},
        {'line': 2, 'text': 'galvanische sowie Lacksysteme', 'need': 'must', 'kind': 'knowledge'},
        {'line': 3, 'text': 'Gute Englischkenntnisse', 'need': 'must', 'kind': 'language'},
    ],
}


def evidence(duty=0.02, req=0.001, role=0.003, domain=0.002):
    return {
        'items': [{'ref': 'd0', 'support': duty, 'quote': 'HubSpot (… Schulungen)', 'source': 'cv.experiences[0]'},
                  {'ref': 'd1', 'support': 0.001, 'quote': 'Trello', 'source': 'skills'},
                  {'ref': 'r0', 'support': req, 'quote': 'Customer success lead', 'source': 'summary'},
                  {'ref': 'r1', 'support': req, 'quote': 'Customer success lead', 'source': 'summary'}],
        'role': {'support': role, 'role': 'Customer Success Manager'},
        'domain': {'support': domain, 'quote': 'Customer success lead', 'source': 'summary', 'field': 'x'},
    }


class SupportTransformTests(unittest.TestCase):
    def test_monotone_and_bounded(self):
        values = [support(p) for p in (0.0, 0.01, 0.02, 0.1, 0.5, 0.8, 0.99, 1.0)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 1.0)
        self.assertEqual(support(None), 0.0)


class ScoringTests(unittest.TestCase):
    def test_posting_with_nothing_to_compare_gets_no_score(self):
        """A title-only or badly parsed posting must not rank from its title alone."""
        empty = {**ANALYSIS, 'duties': [], 'requirements': []}
        strong_title = evidence(role=0.99, domain=0.99)
        result = match_job_v3(JOB, PROFILE, analysis=empty, evidence={**strong_title, 'items': []})
        self.assertTrue(result['unscorable'])
        self.assertFalse(result['pending'])
        self.assertEqual(result['score'], 0)
        self.assertEqual(result['label'], 'Not enough text to score')
        self.assertIn('no duties or requirements', result['unknowns'][0])
        # A posting with real items is scored as before.
        self.assertNotIn('unscorable', match_job_v3(JOB, PROFILE, analysis=ANALYSIS, evidence=strong_title))

    def test_unrelated_shared_word_no_longer_produces_a_strong_match(self):
        # The screenshot case: "Schulungen" appears in both texts, in different fields.
        result = match_job_v3(JOB, PROFILE, analysis=ANALYSIS, evidence=evidence())
        self.assertLess(result['score'], 30)
        self.assertEqual(result['fit_tier'], 'unlikely')
        self.assertEqual(result['matched_skills'], [])
        self.assertIn('Technische Ausbildung im Korrosionsschutz', ' '.join(result['requirement_gaps']))
        self.assertTrue(all(duty['status'] == 'unverified' for duty in result['duty_evidence']['duties']))
        self.assertTrue(all(not duty['evidence_quote'] for duty in result['duty_evidence']['duties']))

    def test_supported_evidence_scores_high_and_cites_cv_lines(self):
        result = match_job_v3(JOB, PROFILE, analysis=ANALYSIS, evidence=evidence(0.999, 0.998, 0.999, 0.999))
        # One of the two duties has no CV evidence, so this is strong but not perfect.
        self.assertGreaterEqual(result['score'], 65)
        self.assertEqual(result['duty_evidence']['duties'][0]['status'], 'direct')
        self.assertEqual(result['duty_evidence']['duties'][0]['evidence_quote'], 'HubSpot (… Schulungen)')
        self.assertEqual(result['job_context']['industry'], 'automotive fasteners')

    def test_ledger_reconciles_with_raw_score_and_rounds(self):
        result = match_job_v3(JOB, PROFILE, analysis=ANALYSIS, evidence=evidence(0.6, 0.3, 0.5, 0.4))
        total = round(sum(entry['contribution'] for entry in result['explanation']), 2)
        self.assertEqual(total, result['calibration']['raw'])
        for entry in result['explanation']:
            self.assertEqual(entry['contribution'], round(entry['contribution'], 2))

    def test_components_carry_possible_points_and_item_counts_for_the_page(self):
        result = match_job_v3(JOB, PROFILE, analysis=ANALYSIS, evidence=evidence(0.9, 0.001, 0.5, 0.4))
        parts = {entry['signal']: entry for entry in result['explanation'] if entry['signal'] in W.COMPONENT_WEIGHTS}
        self.assertAlmostEqual(sum(entry['max_points'] for entry in parts.values()), 100.0, delta=0.05)
        for entry in parts.values():
            self.assertLessEqual(entry['contribution'], entry['max_points'] + 1e-9)
        self.assertEqual((parts['duties']['supported'], parts['duties']['total']), (1, 2))
        self.assertEqual((parts['must']['supported'], parts['must']['total']), (0, 2))
        self.assertIsNone(parts['role']['total'])
        strengths = {row['job_quote']: row.get('strength') for row in result['requirement_observations']}
        self.assertEqual(strengths['Technische Ausbildung im Korrosionsschutz'], 'none')

    def test_score_increases_with_support(self):
        scores = [match_job_v3(JOB, PROFILE, analysis=ANALYSIS, evidence=evidence(p, p, p, p))['score']
                  for p in (0.01, 0.1, 0.3, 0.6, 0.9, 0.99, 0.999)]
        self.assertEqual(scores, sorted(scores))
        # One duty never has evidence in this fixture, so the span stays below 100.
        self.assertGreater(scores[-1] - scores[0], 50)

    def test_language_items_are_left_to_the_gates(self):
        parts = components(ANALYSIS, evidence())
        gated = [row for row in parts['requirements'] if row['gated']]
        self.assertEqual([row['text'] for row in gated], ['Gute Englischkenntnisse'])
        self.assertNotIn('r2', [item['ref'] for item in scored_items(ANALYSIS)])

    def test_missing_model_output_is_pending_and_hidden_from_journey(self):
        from hiring_scraper.app.job_search import application_match_allowed
        result = match_job_v3(JOB, PROFILE)
        self.assertTrue(result['pending'])
        self.assertEqual(result['score'], 0)
        self.assertTrue(result['eligible'])
        profile = SimpleNamespace(matching_preferences={
            'search_area': {'latitude': 49.0, 'longitude': 8.4, 'radius_km': 50},
            'matching_defaults': {'min_match_score': 0}})
        job = SimpleNamespace(locations=[], is_remote=True, work_arrangement='remote', raw_metadata={},
                              title='x', description='', location_text='', date_posted=None, valid_through=None)
        self.assertFalse(application_match_allowed(job, profile, result))

    def test_unanalysed_job_with_an_explicit_conflict_still_cites_it(self):
        job = {**JOB, 'description': 'Deutsch auf Muttersprachniveau zwingend erforderlich.'}
        profile = {**PROFILE, 'language_levels': {'German': 'B1'}}
        result = match_job_v3(job, profile)
        self.assertTrue(result['pending'])
        self.assertFalse(result['eligible'])
        self.assertTrue(any(row['status'] == 'fail' for row in result['prerequisite_conditions']))


class RoleSourceAndSeniorityTests(unittest.TestCase):
    PROFILE = {**PROFILE, 'desired_roles': ['Product Manager'], 'secondary_roles': ['User Researcher'],
               'excluded_terms': ['Head of', 'Sales'], 'seniority_levels': ['junior'],
               'localized_content': {'de': {'desired_roles': ['Produktmanager']}}}

    def role_points(self, role):
        result = match_job_v3(JOB, self.PROFILE, analysis=ANALYSIS,
                              evidence={**evidence(0.6, 0.3, 0.9, 0.4), 'role': {'support': 0.9, 'role': role}})
        entry = next(entry for entry in result['explanation'] if entry['signal'] == 'role')
        return entry['contribution'], entry['profile_source'], result['score']

    def test_desired_roles_count_more_than_past_titles_which_still_count(self):
        desired, source, desired_score = self.role_points('product manager')
        localized, _, _ = self.role_points('Produktmanager')
        secondary, secondary_source, _ = self.role_points('User Researcher')
        past, past_source, past_score = self.role_points('Project & Delivery Manager')
        excluded, _, _ = self.role_points('Head of International Affairs')
        self.assertEqual((source, secondary_source, past_source),
                         ('profile.desired_roles', 'profile.secondary_roles', 'cv.experience'))
        self.assertEqual(desired, localized)
        self.assertGreater(desired, secondary)
        self.assertGreater(secondary, past)
        self.assertGreater(past, excluded)
        self.assertGreater(excluded, 0)
        self.assertGreater(desired_score, past_score)

    def test_title_level_reads_explicit_levels_and_ignores_optional_markers(self):
        cases = {
            'Junior Produktmanager (m/w/d)': 'junior',
            '(Junior) Product Manager (m/w/d)': None,
            'Product Manager (Senior)': None,
            'Sr. AI Engineer (Full Stack)': 'senior',
            'Expert Product Operations Manager': 'lead / staff',
            'Member of Technical Staff - Safety Research': None,
            'Team Lead Operations Automation & AI': 'lead / staff',
            'Director, Delivery': 'head / director',
            'Leiter Einkauf (m/w/d)': 'head / director',
            'Projektleiter Breitbandausbau (w/m/d)': None,
            'VP, Mozilla Studio': 'VP / C-level',
            'Studentische Hilfskraft Future City': 'student / intern',
            'Internal Audit Specialist': None,
            'Leadership & organization': None,
        }
        for title, expected in cases.items():
            found = title_level({'title': title})
            self.assertEqual(found[1] if found else None, expected, title)

    def test_seniority_penalty_is_mild_for_senior_and_grows_for_leadership(self):
        def amount(title):
            penalty = seniority_penalty({'title': title}, self.PROFILE)
            return penalty['amount'] if penalty else 0.0
        self.assertEqual(amount('Junior Product Manager'), 0.0)
        self.assertEqual(amount('Product Manager'), 0.0)
        senior, lead, director, vp = (amount(title) for title in (
            'Senior Product Manager', 'Lead Product Manager', 'Director of Product', 'VP Product'))
        self.assertTrue(0 < senior < lead < director < vp)
        self.assertLessEqual(senior, 3.0)
        self.assertGreater(amount('Werkstudent Produktmanagement'), 0.0)
        self.assertIsNone(seniority_penalty({'title': 'Director of Product'}, {**self.PROFILE, 'seniority_levels': []}))
        self.assertIsNone(seniority_penalty({'title': 'Senior Product Manager'},
                                            {**self.PROFILE, 'seniority_levels': ['junior', 'senior']}))

    def test_seniority_penalty_is_cited_in_the_ledger(self):
        job = {**JOB, 'title': 'Director Technischer Sachbearbeiter'}
        base = match_job_v3(JOB, self.PROFILE, analysis=ANALYSIS, evidence=evidence(0.6, 0.3, 0.5, 0.4))
        result = match_job_v3(job, self.PROFILE, analysis=ANALYSIS, evidence=evidence(0.6, 0.3, 0.5, 0.4))
        entry = next(entry for entry in result['explanation'] if entry['signal'] == 'penalty:seniority')
        self.assertEqual(entry['job_quote'], 'Director')
        self.assertLess(entry['contribution'], 0)
        self.assertLess(result['score'], base['score'])
        self.assertIn('Career level', ' '.join(result['soft_conflicts']))


class EvidenceComputationTests(unittest.TestCase):
    def test_reranker_sees_job_context_and_a_bounded_set_of_cv_lines(self):
        source = units_source(PROFILE)
        units = source['units']
        queries = []

        def embed(texts, kind):
            # Deterministic toy vectors: one dimension per shared keyword.
            keys = ('schulung', 'korrosion', 'galvan', 'customer', 'hubspot')
            return [[1.0 + sum(key in text.casefold() for key in keys[:i + 1]) for i in range(5)] + [0.0] * 379
                    for text in texts]

        def rerank(query, documents):
            queries.append((query, list(documents)))
            return [0.01 for _ in documents]

        from hiring_scraper.matching_v3.evidence import embed_units
        vectors = embed_units(source, embed)
        result = compute_evidence(ANALYSIS, JOB['title'], source, vectors, embed=embed, rerank=rerank)
        self.assertEqual([item['ref'] for item in result['items']], ['d0', 'd1', 'r0', 'r1'])
        item_queries = queries[:4]
        for query, documents in item_queries:
            self.assertIn('surface engineering / corrosion protection', query)
            self.assertLessEqual(len(documents), CANDIDATES_PER_ITEM)
            self.assertTrue(set(documents) <= {unit['text'] for unit in units})
        self.assertIsNotNone(result['role'])
        self.assertIsNotNone(result['domain'])

    def test_reranker_always_sees_work_bullets_when_skill_lines_win_retrieval(self):
        units = [{'source': f'skill_evidence.s{i}', 'text': f'skill line {i}'} for i in range(10)]
        units += [{'source': 'cv.experiences[0]', 'text': 'Coordinated a company-wide initiative with four departments.'}]
        source = {'units': units, 'roles': []}
        seen = []

        def embed(texts, kind):
            # Skill lines are closest to every query; the work bullet is furthest away.
            return [[0.0 if 'Coordinated' in text else 1.0, 1.0] + [0.0] * 382 for text in texts]

        def rerank(query, documents):
            seen.append(list(documents))
            return [0.9 if 'Coordinated' in text else 0.01 for text in documents]

        from hiring_scraper.matching_v3.evidence import embed_units
        analysis = {'requirements': [{'text': 'Erfahrung in der Koordination von Teams', 'need': 'must', 'kind': 'experience'}]}
        result = compute_evidence(analysis, 'Product Owner', source, embed_units(source, embed), embed=embed, rerank=rerank)
        self.assertEqual(len(seen[0]), CANDIDATES_PER_ITEM)
        self.assertIn(units[-1]['text'], seen[0])
        self.assertEqual(result['items'][0]['source'], 'cv.experiences[0]')

    def test_personal_traits_are_not_scored_but_experience_claims_are(self):
        analysis = {'requirements': [
            {'text': 'Strukturierte und selbstständige Arbeitsweise', 'need': 'must', 'kind': 'skill'},
            {'text': 'Ausgeprägte Kommunikations- und Abstimmungsfähigkeiten', 'need': 'must', 'kind': 'skill'},
            {'text': 'Erfahrung in der fachlichen Steuerung oder Koordination von Teams', 'need': 'must', 'kind': 'experience'},
            {'text': 'Independent contractor setup', 'need': 'must', 'kind': 'other'},
        ]}
        self.assertEqual([item['ref'] for item in scored_items(analysis)], ['r2', 'r3'])
        self.assertTrue(is_soft_requirement({'text': 'Excellent communication skills', 'kind': 'skill'}))
        self.assertFalse(is_soft_requirement({'text': '3+ years of experience in stakeholder communication', 'kind': 'skill'}))

    def test_rerank_queries_distinguish_duties_and_requirements(self):
        self.assertIn('Task the candidate will perform', rerank_query({'type': 'duty', 'text': 'x'}, 'ctx'))
        self.assertIn('Requirement the candidate must meet', rerank_query({'type': 'requirement', 'text': 'x'}, 'ctx'))

    def test_budget_keeps_mandatory_formal_requirements_and_reports_the_rest(self):
        analysis = {'duties': [{'text': f'duty {i}'} for i in range(14)],
                    'requirements': [{'text': f'nice {i}', 'need': 'nice', 'kind': 'skill'} for i in range(14)]
                    + [{'text': 'Zertifizierung als Schweißfachmann', 'need': 'must', 'kind': 'certification'}]}
        refs = {item['ref'] for item in scored_items(analysis)}
        self.assertIn('r14', refs)
        evidence = {'items': [{'ref': ref, 'support': 0.5, 'quote': '', 'source': ''} for ref in refs],
                    'role': None, 'domain': None}
        result = match_job_v3(JOB, PROFILE, analysis=analysis, evidence=evidence)
        self.assertTrue(any('were not checked' in reason for reason in result['unknowns']))

    def test_item_count_is_bounded_and_keeps_requirements(self):
        analysis = {'duties': [{'text': f'duty {i}'} for i in range(40)],
                    'requirements': [{'text': f'req {i}', 'need': 'must', 'kind': 'skill'} for i in range(10)]}
        items = scored_items(analysis)
        self.assertEqual(len(items), MAX_SCORED_ITEMS)
        self.assertEqual(sum(item['type'] == 'requirement' for item in items), 10)


class WeightsTests(unittest.TestCase):
    def test_component_weights_sum_to_one_and_language_visa_stays_small(self):
        self.assertAlmostEqual(sum(W.COMPONENT_WEIGHTS.values()), 1.0)
        self.assertLessEqual(W.SOFT_PENALTIES['language_visa_flag_max'], 5.0)


if __name__ == '__main__':
    unittest.main()
