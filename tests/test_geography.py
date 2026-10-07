import unittest

from hiring_scraper.geography import (
    build_osm_radius_query,
    build_osm_state_query,
    haversine_m,
    normalize_german_state,
    osm_element_coordinates,
    resolve_nominatim_city,
    state_for_point,
)


class GeographyTests(unittest.TestCase):
    def test_radius_query_uses_circle_coordinates_and_business_tags(self):
        query = build_osm_radius_query(49.0069, 8.4037, 5000)
        self.assertIn("around:5000,49.0069,8.4037", query)
        self.assertIn('[office]', query)
        self.assertIn('[craft]', query)
        self.assertIn('[industrial]', query)

    def test_state_aliases_resolve_to_official_name_and_admin_area(self):
        self.assertEqual(normalize_german_state('Baden-Wuerttemberg'), 'Baden-Württemberg')
        query = build_osm_state_query('Baden-Württemberg', office_value='it')
        self.assertIn('["admin_level"="4"]', query)
        self.assertIn('["name"="Baden-Württemberg"]', query)
        self.assertIn('["office"="it"]', query)

    def test_osm_points_support_nodes_and_way_centers(self):
        self.assertEqual(osm_element_coordinates({'type':'node','lat':49.0,'lon':8.0}), (49.0,8.0))
        self.assertEqual(osm_element_coordinates({'type':'way','center':{'lat':49.1,'lon':8.1}}), (49.1,8.1))
        self.assertIsNone(osm_element_coordinates({'type':'relation'}))

    def test_nominatim_result_provides_radius_center_and_state(self):
        result = resolve_nominatim_city([{
            'lat':'49.0068705','lon':'8.4034195','display_name':'Karlsruhe, Baden-Württemberg, Deutschland',
            'osm_type':'relation','osm_id':62518,
            'address':{'city':'Karlsruhe','state':'Baden-Württemberg','country_code':'de'}
        }], 'Karlsruhe')
        self.assertEqual(result['city'],'Karlsruhe')
        self.assertEqual(result['state'],'Baden-Württemberg')
        self.assertEqual((result['lat'],result['lon']),(49.0068705,8.4034195))

    def test_nominatim_rejects_a_non_german_or_empty_result(self):
        with self.assertRaises(ValueError): resolve_nominatim_city([], 'Springfield')
        with self.assertRaises(ValueError):
            resolve_nominatim_city([{'lat':'0','lon':'0','address':{'country_code':'us'}}], 'Springfield')

    def test_state_lookup_uses_geojson_polygon_and_respects_holes(self):
        features = [{
            'properties':{'gen':'Baden-Württemberg','bez':'Land'},
            'geometry':{'type':'Polygon','coordinates':[
                [[8,48],[9,48],[9,50],[8,50],[8,48]],
                [[8.4,49],[8.5,49],[8.5,49.5],[8.4,49.5],[8.4,49]],
            ]},
        }]
        self.assertEqual(state_for_point(49,8.2,features), 'Baden-Württemberg')
        self.assertIsNone(state_for_point(49.2,8.45,features))

    def test_state_lookup_supports_multipolygon_and_unknown_points(self):
        features = [{
            'properties':{'gen':'Bayern','bez':'Freistaat'},
            'geometry':{'type':'MultiPolygon','coordinates':[[[[10,47],[11,47],[11,48],[10,48],[10,47]]]]},
        }, {
            'properties':{'gen':'Hamburg','bez':'Freie und Hansestadt'},
            'geometry':{'type':'MultiPolygon','coordinates':[[[[9,53],[11,53],[11,55],[9,55],[9,53]]]]},
        }]
        self.assertEqual(state_for_point(47.5,10.5,features), 'Bayern')
        self.assertEqual(state_for_point(53.55,9.99,features), 'Hamburg')
        self.assertIsNone(state_for_point(49,8,features))

    def test_haversine_returns_distance_in_metres(self):
        self.assertAlmostEqual(haversine_m(49.0069,8.4037,49.0069,8.4037), 0)
        self.assertAlmostEqual(haversine_m(0,0,0,1), 111_195, delta=300)

    def test_rejects_invalid_spatial_inputs(self):
        for args in [(-91,8,5000),(49,181,5000),(49,8,0),(49,8,300_000)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                build_osm_radius_query(*args)
        with self.assertRaises(ValueError):
            normalize_german_state('France')


if __name__ == '__main__':
    unittest.main()
