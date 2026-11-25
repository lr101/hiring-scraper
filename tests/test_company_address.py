import unittest
from types import SimpleNamespace

from hiring_scraper.app.html_feed_rules import company_address_location as _company_address_location


class CompanyAddressLocationTests(unittest.TestCase):
    def company(self, **changes):
        values = dict(latitude=49.48, longitude=8.44, domain="handwerk.example", location_precision="point",
                      country_code="DE", category="company", state="Rheinland-Pfalz")
        values.update(changes)
        return SimpleNamespace(**values)

    def session(self, companies_on_domain=1):
        return SimpleNamespace(scalar=lambda statement: companies_on_domain)

    def test_a_single_site_employer_lends_its_address(self):
        location = _company_address_location(self.session(), self.company())
        self.assertEqual(location, {"label": "Company address, Rheinland-Pfalz", "latitude": 49.48,
                                    "longitude": 8.44, "precision": "company_address", "country_code": "DE"})

    def test_agencies_groups_and_vague_points_do_not(self):
        self.assertIsNone(_company_address_location(self.session(), self.company(category="employment_agency")))
        self.assertIsNone(_company_address_location(self.session(companies_on_domain=12), self.company()))
        self.assertIsNone(_company_address_location(self.session(), self.company(location_precision="city_centroid")))
        self.assertIsNone(_company_address_location(self.session(), self.company(country_code="AT")))
        # No country and no German state is no evidence of a German site.
        self.assertIsNone(_company_address_location(self.session(), self.company(country_code=None, state=None)))
        self.assertIsNone(_company_address_location(self.session(), self.company(country_code=None, state="Tirol")))
        self.assertIsNotNone(_company_address_location(self.session(), self.company(country_code=None)))
        self.assertIsNone(_company_address_location(self.session(), self.company(domain=None)))
        self.assertIsNone(_company_address_location(self.session(), self.company(latitude=None)))


if __name__ == "__main__":
    unittest.main()
