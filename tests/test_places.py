import unittest

from hiring_scraper.places import parse_places, places_in_segments, title_place, trailing_place


class PlaceReadingTests(unittest.TestCase):
    def test_lines_that_are_german_places(self):
        cases = {
            "Dortmund": ["Dortmund"],
            "Düsseldorf, Dortmund, Bremen": ["Düsseldorf", "Dortmund", "Bremen"],
            "Meerane | Dresden": ["Meerane", "Dresden"],
            "Mannheim, hybrid": ["Mannheim"],
            "München (Deutschland)": ["München"],
            "10557 Berlin": ["Berlin"],
            "Bredenbekstraße 42, 22397 Hamburg, Deutschland": ["Hamburg"],
            "Braunschweig, DE, 38037": ["Braunschweig"],
            "Haren (Ems), Deutschland": ["Haren (Ems)"],
            "Egling a. d. Paar": ["Egling a. d. Paar"],
            "Geisenheim-Marienthal": ["Geisenheim-Marienthal"],
            "Schwerte": ["Schwerte"],
            "📍 Obertshausen": ["Obertshausen"],
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(parse_places(line), expected)

    def test_words_codes_and_prose_are_not_places(self):
        for line in ("Junior", "Vollzeit", "Finanzen", "PLZ", "IT", "KI", "IT und Software",
                     "Lehre", "Stelle", "einer VGH Agentur", "Wir suchen in Berlin", "Jobtitel, Firmenname, ID"):
            with self.subTest(line=line):
                self.assertEqual(parse_places(line), [])

    def test_foreign_places_stay_readable_by_their_country(self):
        self.assertEqual(parse_places("Pune, India"), ["Pune, India"])
        self.assertEqual(parse_places("Essen, BE"), ["Essen, BE"])
        # Each group keeps its own country.
        self.assertEqual(parse_places("Essen, BE; Berlin", labeled=True), ["Essen, BE", "Berlin"])
        self.assertEqual(parse_places("Essen, BE/Berlin", labeled=True), ["Essen, BE", "Berlin"])
        self.assertEqual(parse_places("IT/TK", labeled=True), [])
        self.assertEqual(parse_places("Hong Kong"), [])
        self.assertEqual(parse_places("Schiphol-Rijk", labeled=True), ["Schiphol-Rijk"])

    def test_places_inside_fact_lines_titles_and_run_on_cards(self):
        self.assertEqual(places_in_segments("opta data | Vollzeit | Essen | Hybrid"), ["Essen"])
        self.assertEqual(places_in_segments("58239 Schwerte · AÜ mit Übernahmeoption"), ["Schwerte"])
        self.assertEqual(trailing_place("25.11.2026 Vetter Pharma-Fertigung GmbH & Co. KG Saarlouis"), "Saarlouis")
        self.assertEqual(title_place("Sales Manager (all genders) in Hamburg"), "Hamburg")
        self.assertEqual(title_place("Ausbildung Fachkraft für Lagerlogistik in Dormagen (m/w/d)"), "Dormagen")
        self.assertEqual(title_place("Steuerberater (m/w/d) am Standort Rödinghausen"), "Rödinghausen")
        self.assertEqual(title_place("Anlagenmechaniker SHK (m/w/d) Hechingen"), "Hechingen")
        self.assertEqual(title_place("Siegburg – Technischer Produktdesigner (w/m/d)"), "Siegburg")
        self.assertIsNone(title_place("Servicetechniker (m/w/d) für unser Filialnetz in Vollzeit"))


if __name__ == "__main__":
    unittest.main()
