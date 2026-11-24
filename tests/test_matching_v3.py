"""Matcher v3: reranker-checked evidence, exact ledger, pending jobs stay hidden."""
import unittest
from types import SimpleNamespace

from hiring_scraper.matching_v3 import match_job_v3
from hiring_scraper.matching_v3 import weights as W
from hiring_scraper.matching_v3.evidence import (
    MAX_SCORED_ITEMS, compute_evidence, rerank_query, scored_items, units_source,
)
from hiring_scraper.matching_v3.score import components, support

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


class EvidenceComputationTests(unittest.TestCase):
    def test_reranker_sees_job_context_and_only_the_closest_cv_lines(self):
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
            self.assertLessEqual(len(documents), 3)
            self.assertTrue(set(documents) <= {unit['text'] for unit in units})
        self.assertIsNotNone(result['role'])
        self.assertIsNotNone(result['domain'])

    def test_rerank_queries_distinguish_duties_and_requirements(self):
        self.assertIn('Task the candidate will perform', rerank_query({'type': 'duty', 'text': 'x'}, 'ctx'))
        self.assertIn('Requirement the candidate must meet', rerank_query({'type': 'requirement', 'text': 'x'}, 'ctx'))

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
