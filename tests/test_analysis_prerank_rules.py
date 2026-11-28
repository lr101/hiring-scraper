"""Cheap pre-rank signals and prompt trimming that decide what the LLM reads (no database)."""
import unittest

from hiring_scraper.app.analysis_gate import TITLE_BOOST, prerank_score, title_role_fit, title_roles
from hiring_scraper.app.models import UserProfile
from hiring_scraper.job_analysis import prompt_lines, prompt_messages

PROFILE = {
    'desired_roles': ['Product Owner', 'Projektleiter Produktion'],
    'secondary_roles': ['Business Analyst'],
    'excluded_terms': ['Head'],
    'cv_career_facts': {'experiences': [{'title': 'Data Analyst'}, {'title': 'Head of Operations'}]},
    'localized_content': {'en': {'desired_roles': ['Production Project Manager']}},
}


class TitleRoleFitTests(unittest.TestCase):
    def setUp(self):
        self.roles = title_roles(PROFILE)

    def fit(self, title):
        return title_role_fit(title, self.roles)

    def test_desired_secondary_and_past_titles_lift_by_source(self):
        self.assertEqual(self.fit('Senior Product Owner (m/w/d)'), 1.0)
        self.assertEqual(self.fit('Production Project Manager'), 1.0)  # localized desired role
        self.assertEqual(self.fit('Business Analyst Payments'), 0.85)
        self.assertEqual(self.fit('Data Analyst'), 0.7)
        self.assertGreater(self.fit('Senior Product Owner'), self.fit('Business Analyst'))

    def test_german_compounds_and_inflections_match(self):
        self.assertEqual(self.fit('Projektleiterin Produktionsanlauf'), 1.0)

    def test_one_generic_word_counts_little_and_unrelated_titles_nothing(self):
        self.assertEqual(self.fit('Product Designer'), 0.25)
        self.assertEqual(self.fit('CNC-Fräser (m/w/d)'), 0.0)
        self.assertEqual(self.fit('Lagerhelfer'), 0.0)

    def test_roles_find_titles_in_the_other_language(self):
        roles = [(role, 1.0) for role in ('Produktmanager', 'Requirements Manager', 'Technischer Projektmanager')]
        self.assertEqual(title_role_fit('Product Manager (m/w/d)', roles), 1.0)
        self.assertEqual(title_role_fit('Anforderungsmanagerin', roles), 1.0)
        self.assertEqual(title_role_fit('Technical Project Manager – Packaging', roles), 1.0)
        self.assertEqual(title_role_fit('Produktionsmitarbeiter', roles), 0.0)  # "produktion" is not "produkt"
        self.assertEqual(title_role_fit('Technischer Produktdesigner', roles), 0.25)  # one of two words

    def test_german_tab_roles_reach_matching_but_other_translations_do_not(self):
        profile = UserProfile(preferences={
            'desired_roles': ['Product Manager'],
            'localized_content': {'de': {'desired_roles': ['Anforderungsmanager'], 'summary': 'Deutsch'},
                                  'en': {'summary': 'English only'}}})
        preferences = profile.matching_preferences
        self.assertEqual(preferences['localized_content'], {'de': {'desired_roles': ['Anforderungsmanager']}})
        self.assertEqual(title_role_fit('Anforderungsmanager (m/w/d)', title_roles(preferences)), 1.0)

    def test_past_titles_with_excluded_terms_never_lift(self):
        self.assertEqual(self.fit('Head of Operations'), 0.0)

    def test_score_adds_the_best_profile_boost(self):
        self.assertAlmostEqual(prerank_score(0.83, 'Product Owner', [[], self.roles]), 0.83 + TITLE_BOOST)
        self.assertEqual(prerank_score(0.83, 'Product Owner', []), 0.83)


class PromptTrimTests(unittest.TestCase):
    def test_benefit_lines_are_left_out_but_numbering_is_kept(self):
        lines = ['Job title: Analyst', 'You analyse data.', 'We offer 30 days of vacation.', 'You know SQL.']
        scopes = ['title', 'duties', 'benefits', 'requirements']
        self.assertEqual(prompt_lines(lines, scopes), [lines[0], lines[1], '', lines[3]])
        user = prompt_messages(prompt_lines(lines, scopes))[1]['content']
        self.assertEqual(user, '[0] Job title: Analyst\n[1] You analyse data.\n[3] You know SQL.')
        self.assertEqual(prompt_lines(lines, None), lines)


if __name__ == '__main__':
    unittest.main()
