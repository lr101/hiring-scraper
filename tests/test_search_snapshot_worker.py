"""Dependency boundary for the standalone snapshot worker."""

import subprocess
import sys
import unittest


class SearchSnapshotWorkerTests(unittest.TestCase):
    def test_worker_import_does_not_load_http_api(self):
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; import hiring_scraper.app.search_snapshot_worker; "
                "assert 'hiring_scraper.app.api' not in sys.modules",
            ],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
