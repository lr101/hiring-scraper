"""Keep the persistent PostgreSQL cluster on its existing libc provider."""
from pathlib import Path
import unittest


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


if __name__ == "__main__":
    unittest.main()
