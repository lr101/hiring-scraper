"""The installable archive is deterministic and excludes local/private files."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]


class PackagingTests(unittest.TestCase):
    def test_builds_reproducible_installable_archive_with_checksum(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            command = [sys.executable, str(ROOT / 'scripts/build_extension.py'), '--output', str(destination)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            archive = destination / 'radius-application-companion.zip'
            original = archive.read_bytes()
            with ZipFile(archive) as package:
                names = set(package.namelist())
                self.assertIn('extension/manifest.json', names)
                self.assertIn('extension/background.js', names)
                self.assertIn('INSTALL.md', names)
                self.assertFalse(any('private' in name or 'node_modules' in name or 'test_' in name for name in names))
                manifest = json.loads(package.read('extension/manifest.json'))
                self.assertEqual(manifest['name'], 'Radius Application Companion')
                self.assertNotIn('host_permissions', manifest)
                self.assertIsNone(package.testzip())
                for name in names:
                    if name.startswith('extension/'):
                        self.assertEqual(package.read(name), (ROOT / 'extensions/application-companion' / name.removeprefix('extension/')).read_bytes())
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(original, archive.read_bytes())
            self.assertEqual((destination / 'radius-application-companion.zip.sha256').read_text(), hashlib.sha256(original).hexdigest() + '  radius-application-companion.zip\n')
