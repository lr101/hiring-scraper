import ast
import unittest
from pathlib import Path


class PostgresImageTests(unittest.TestCase):
    def test_postgres_base_keeps_the_existing_libc_collation_provider(self):
        dockerfile = Path(__file__).parents[1] / "docker" / "Dockerfile.postgres"
        base = next(line for line in dockerfile.read_text().splitlines()
                    if line.startswith("ARG POSTGIS_IMAGE="))

        self.assertEqual(
            base,
            "ARG POSTGIS_IMAGE=postgis/postgis:17-3.5",
            "The persisted cluster was initialized on Debian/glibc; changing to an "
            "Alpine/musl base changes libc collation behavior and can invalidate indexes.",
        )

    def test_collation_upgrade_reindexes_before_refreshing_the_database_version(self):
        migration_path = (Path(__file__).parents[1] / "hiring_scraper" / "app" / "migrations"
                          / "versions" / "0025_postgres_collation_repair.py")
        self.assertTrue(migration_path.exists(), "The PostgreSQL collation repair migration is missing")
        migration = migration_path.read_text()
        module = ast.parse(migration)
        executable_sql = []
        for node in ast.walk(module):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "op"
                    and node.args):
                continue
            statement = ast.get_source_segment(migration, node.args[0]) or ""
            executable_sql.append((node.lineno, statement))
        executable_sql.sort()
        rebuild = next((line for line, statement in executable_sql
                        if "REINDEX DATABASE CONCURRENTLY" in statement), None)
        refresh = next((line for line, statement in executable_sql
                        if "REFRESH COLLATION VERSION" in statement), None)
        self.assertIsNotNone(rebuild, "The migration does not execute a concurrent database reindex")
        self.assertIsNotNone(refresh, "The migration does not execute a collation version refresh")
        self.assertLess(rebuild, refresh, "Refresh the recorded version only after rebuilding indexes")


if __name__ == "__main__":
    unittest.main()
