import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hiring_scraper import __main__ as runner


class CrawlRunnerTests(unittest.TestCase):
    def test_parallel_runner_checkpoints_and_emits_results_in_seed_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seeds = [{"name": f"Company {index}", "website": f"https://company-{index}.example/",
                      "osm_source_id": f"node/{index}"} for index in range(4)]
            seed_path = root / "seeds.json"
            out = root / "run"
            seed_path.write_text(json.dumps(seeds), encoding="utf-8")

            def fake_discover(seed, client, max_pages, max_depth):
                return {**seed, "pages": [{"url": seed["website"], "classification": "career_content"}],
                        "boards": [], "status": "career_content_found"}

            argv = ["hiring-scraper", "--seeds", str(seed_path), "--out", str(out),
                    "--workers", "2", "--checkpoint-every", "1"]
            with patch("sys.argv", argv), patch.object(runner, "discover", side_effect=fake_discover):
                runner.main()

            results = json.loads((out / "results.json").read_text(encoding="utf-8"))
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual([item["osm_source_id"] for item in results], [f"node/{i}" for i in range(4)])
            self.assertEqual(summary["companies"], 4)
            self.assertEqual(summary["statuses"], {"career_content_found": 4})
            self.assertTrue((out / "companies.csv").exists())
            self.assertTrue((out / "run.json").exists())


if __name__ == "__main__":
    unittest.main()
