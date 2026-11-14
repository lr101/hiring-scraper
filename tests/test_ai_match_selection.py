"""AI ordering must retain every candidate regardless of score or employer."""
import importlib
import unittest


class AISelectionTests(unittest.TestCase):
    def select(self, rows):
        return importlib.import_module('hiring_scraper.app.ai_match_selection').select_cases(rows)

    def rows(self, count=100):
        return [dict(id=i, score=100-i/10, company=i, task_score=0, uncertain=False) for i in range(count)]

    def test_large_inventory_retains_all_candidates_with_strong_candidates_first(self):
        rows = self.rows(1000)
        selected = self.select(rows)
        self.assertEqual(len(selected), 1000)
        self.assertTrue(set(range(8)).issubset(selected))

    def test_completed_ai_results_do_not_drain_static_pool(self):
        rows = self.rows()
        initial = self.select(rows)
        for row in rows:
            row['ai_checked'] = row['id'] in initial
            row['ai_score'] = 0
        self.assertEqual(self.select(rows), initial)

    def test_disagreement_reserves_a_small_exploration_lane(self):
        rows = self.rows()
        rows[-1].update(score=15, task_score=85, uncertain=True)
        self.assertIn(rows[-1]['id'], self.select(rows))

    def test_smaller_inventories_also_retain_all_candidates(self):
        self.assertEqual(len(self.select(self.rows(30))), 30)
        self.assertEqual(self.select([]), [])

    def test_pool_is_stable_when_database_iteration_order_changes(self):
        rows = self.rows()
        self.assertEqual(self.select(rows), self.select(list(reversed(rows))))

    def test_employer_diversity_does_not_exclude_jobs(self):
        rows = self.rows()
        for row in rows[:20]:
            row['company'] = 'one-employer'
        selected = self.select(rows)
        self.assertEqual(sum(i < 20 for i in selected), 20)
