"""The PostgreSQL semantic search query builds against the pinned model revision."""
import unittest
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from hiring_scraper.app import search_snapshots
from hiring_scraper.semantic_matching import MODEL_KEY


class SemanticJobSearchQueryTests(unittest.TestCase):
    def test_query_filters_vectors_by_current_model_revision(self):
        session = MagicMock()
        session.scalar.return_value = 0
        session.execute.return_value.all.return_value = []
        session.scalars.return_value.all.return_value = []
        page = search_snapshots.semantic_jobs_page(
            session, [0.0] * 384, latitude=49.0, longitude=8.4, radius_km=25,
            place='Karlsruhe', country='de', location_scope='area', work_style='any',
            company_id=None, state=None, employer_country=None, profile=None,
            offset=0, limit=20, job_status=None)
        self.assertIsInstance(page, dict)
        count_query = session.scalar.call_args_list[0].args[0]
        compiled = count_query.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': False})
        self.assertIn(MODEL_KEY, compiled.params.values())


if __name__ == '__main__':
    unittest.main()
