"""Prevent a navigation label being counted as a fetched career page."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'experiments'))
from careers import Links, career_links

class CareerLinksTest(unittest.TestCase):
    def test_empty_navigation_label_does_not_hide_real_career_page(self):
        parser=Links()
        parser.feed('<a href="">Karriere</a><a href="/ueber-uns/karriere/berufserfahrene">Stellenangebote</a>')
        self.assertEqual(career_links(parser,'https://example.org/'),
                         ['https://example.org/ueber-uns/karriere/berufserfahrene'])
