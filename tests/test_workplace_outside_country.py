"""Foreign-only postings are recognised only from positive evidence of a workplace abroad."""
import unittest
from types import SimpleNamespace

from hiring_scraper.app.board_scope import workplace_outside_country


def job(location_text=None, locations=(), *, remote=False, raw_metadata=None):
    return SimpleNamespace(
        location_text=location_text,
        locations=[SimpleNamespace(label=label, country_code=code, latitude=lat, longitude=lon)
                   for label, code, lat, lon in locations],
        work_arrangement='remote' if remote else None, is_remote=remote, raw_metadata=raw_metadata or {})


class WorkplaceOutsideCountryTests(unittest.TestCase):
    def test_named_foreign_country_code_or_coordinates_count_as_abroad(self):
        self.assertTrue(workplace_outside_country(job('Kraków, Poland'), 'DE'))
        self.assertTrue(workplace_outside_country(job('Remote France'), 'DE'))
        self.assertTrue(workplace_outside_country(job('Cebu', [('Cebu', 'PH', 10.3, 123.9)]), 'DE'))
        self.assertTrue(workplace_outside_country(job('Sofia', [('Sofia', None, 42.7, 23.3)]), 'DE'))

    def test_unknown_or_german_places_are_never_abroad(self):
        for value in ('Bad Vilbel', 'Pfinztal', 'Weil der Stadt', 'Amsterdam', None, 'Karlsruhe',
                      'Parsdorf, Bavaria, Germany', 'Company address, Nordrhein-Westfalen'):
            self.assertFalse(workplace_outside_country(job(value), 'DE'), value)
        self.assertFalse(workplace_outside_country(job('Pfinztal', [('Pfinztal', None, 49.0, 8.5)]), 'DE'))

    def test_one_german_or_unknown_workplace_keeps_a_multi_location_posting(self):
        self.assertFalse(workplace_outside_country(job('Amsterdam, Netherlands; Berlin, Germany'), 'DE'))
        self.assertFalse(workplace_outside_country(job('Kraków, Poland; Ettlingen'), 'DE'))

    def test_remote_postings_follow_their_country_restriction(self):
        self.assertTrue(workplace_outside_country(
            job('Remote', remote=True, raw_metadata={'remote_country_codes': ['US']}), 'DE'))
        self.assertFalse(workplace_outside_country(
            job('Remote', remote=True, raw_metadata={'remote_country_codes': ['US', 'DE']}), 'DE'))
        self.assertFalse(workplace_outside_country(job('Remote', remote=True), 'DE'))

    def test_no_country_disables_the_rule(self):
        self.assertFalse(workplace_outside_country(job('Kraków, Poland'), None))


if __name__ == '__main__':
    unittest.main()
