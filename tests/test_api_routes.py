import unittest

from hiring_scraper.app.api import app


class ApiRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = app.openapi()['paths']

    def test_journey_list_takes_its_own_query_parameters(self):
        operation = self.paths['/api/v1/applications']['get']
        names = {param['name'] for param in operation.get('parameters', [])}
        self.assertEqual(names, {'profile_ids', 'statuses', 'sort', 'offset', 'limit'})

    def test_no_route_takes_a_session_from_the_request(self):
        # A route decorator placed on a helper turns its `session` argument into a
        # required query parameter, and every request then fails with 422.
        for path, operations in self.paths.items():
            for method, operation in operations.items():
                with self.subTest(route=f'{method.upper()} {path}'):
                    names = {param['name'] for param in operation.get('parameters', [])}
                    self.assertNotIn('session', names)


if __name__ == '__main__':
    unittest.main()
