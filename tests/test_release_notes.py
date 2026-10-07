"""Exercise release validation using real temporary project files."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci/release_notes.py"
CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Work for the next release.

## [0.2.0] - 2026-10-07

### Fixed

- Preserve saved profiles.

## [0.1.0] - 2026-09-01

### Added

- Initial application.
"""


class ReleaseNotesTests(unittest.TestCase):
    def run_check(self, changelog=CHANGELOG, version="0.2.0", tag=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
            (root / "pyproject.toml").write_text(
                f'[project]\nversion = "{version}"\n', encoding="utf-8"
            )
            args = [sys.executable, str(SCRIPT), "--root", str(root)]
            if tag is not None:
                args.extend(["--tag", tag])
            return subprocess.run(args, capture_output=True, text=True)

    def test_extracts_only_tagged_release_body(self):
        result = self.run_check(tag="v0.2.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "### Fixed\n\n- Preserve saved profiles.\n")

    def test_allows_unreleased_only_before_first_release(self):
        result = self.run_check("# Changelog\n\n## [Unreleased]\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_bad_tags(self):
        for tag in ["v02.0.0", "0.2.0", "v0.2", "v0.2.0-beta.1", "vanything"]:
            with self.subTest(tag=tag):
                result = self.run_check(tag=tag)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("stable", result.stderr)

    def test_rejects_version_mismatch(self):
        result = self.run_check(version="0.3.0", tag="v0.2.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pyproject.toml", result.stderr)

    def test_rejects_missing_release_entry(self):
        result = self.run_check(version="0.3.0", tag="v0.3.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("entry", result.stderr)

    def test_rejects_invalid_dates_and_duplicate_sections(self):
        for changelog in [
            CHANGELOG.replace("2026-10-07", "2026-02-30"),
            CHANGELOG + "\n## [0.2.0] - 2026-10-07\n- Duplicate.\n",
            CHANGELOG.replace("## [Unreleased]", "## Unreleased"),
        ]:
            with self.subTest(changelog=changelog):
                self.assertNotEqual(self.run_check(changelog).returncode, 0)

    def test_rejects_empty_release_even_with_headings_or_comments(self):
        for body in ["", "### Fixed\n", "<!-- - TODO -->\n", "- TODO\n"]:
            changelog = "## [Unreleased]\n\n## [0.2.0] - 2026-10-07\n" + body
            with self.subTest(body=body):
                self.assertNotEqual(self.run_check(changelog, tag="v0.2.0").returncode, 0)


if __name__ == "__main__":
    unittest.main()
