"""CI sharding must execute every discovered test exactly once and report failures."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


RUNNER = Path(__file__).resolve().parents[1] / 'scripts/ci/test_shard.py'


class ShardingTests(unittest.TestCase):
    def run_shard(self, directory, index, count, cwd=None):
        return subprocess.run([
            sys.executable, str(RUNNER), '--start-directory', str(directory),
            '--shard', str(index), '--shards', str(count),
        ], capture_output=True, text=True, cwd=cwd)

    def test_discovered_tests_can_import_repository_helpers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'support.py').write_text('value = 42\n')
            tests = root / 'tests'
            tests.mkdir()
            (tests / 'test_example.py').write_text('''import unittest
from support import value
class Example(unittest.TestCase):
    def test_helper(self): self.assertEqual(value, 42)
''')
            result = self.run_shard(tests, 1, 1, cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_shards_cover_every_test_once_and_propagate_failures(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'test_example.py').write_text('''import unittest
class Example(unittest.TestCase):
    def test_a(self): pass
    def test_b(self): self.fail('real failure')
    def test_c(self): pass
    def test_d(self): pass
    def test_e(self): pass
''')
            results = [self.run_shard(root, index, 2) for index in (1, 2)]
            self.assertEqual([result.returncode for result in results], [0, 1])
            combined = ''.join(result.stderr for result in results)
            executed = '\n'.join(line for line in combined.splitlines() if ' ... ' in line)
            for name in 'abcde':
                self.assertEqual(executed.count(f'test_{name} (test_example.Example.test_{name})'), 1)
            self.assertIn('real failure', combined)

    def test_invalid_or_empty_shards_fail_instead_of_passing_without_checks(self):
        with TemporaryDirectory() as directory:
            for index, count in [(0, 2), (3, 2), (1, 0), (1, 2)]:
                with self.subTest(index=index, count=count):
                    self.assertNotEqual(self.run_shard(directory, index, count).returncode, 0)

    def test_discovery_error_cannot_be_assigned_to_an_unrun_shard(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'test_broken.py').write_text('raise ImportError("broken dependency")')
            for index in (1, 2):
                result = self.run_shard(root, index, 2)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('broken dependency', result.stderr)
