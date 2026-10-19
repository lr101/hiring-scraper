"""Bound repeated Unicode work during large vacancy scope scans."""
import cProfile
import pstats
import unittest

from hiring_scraper.app.board_scope import location_text_countries, location_text_matches


class LocationTextPerformanceTests(unittest.TestCase):
    def test_repeated_country_checks_do_not_renormalize_all_static_aliases(self):
        profiler = cProfile.Profile()
        with profiler:
            for _ in range(100):
                self.assertEqual(location_text_countries('Karlsruhe, Germany'), {'DE'})
                self.assertTrue(location_text_matches('Muenchen, DE', 'München'))
                self.assertFalse(location_text_matches('Frankfurt an der Oder', 'Frankfurt'))
        # Unicode normalization is real work in the hot path. Allow ample cold
        # setup, but not tens of thousands of repeated static alias conversions.
        normalization_calls = sum(stats[1] for key, stats in pstats.Stats(profiler).stats.items()
                                  if 'unicodedata.normalize' in key[2])
        self.assertLess(normalization_calls, 2000)
