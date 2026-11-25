"""Check named-volume permissions using the production UID without Docker."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('radius_entrypoint', Path(__file__).resolve().parents[1] / 'docker/entrypoint.py')
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


class CodexStatePermissionsTests(unittest.TestCase):
    @unittest.skipUnless(os.geteuid() == 0, 'Production UID check requires root')
    def test_fresh_and_existing_volume_writable_after_privilege_drop(self):
        with tempfile.TemporaryDirectory(prefix='radius-volume-') as folder:
            Path(folder).chmod(0o755)
            root = Path(folder) / 'codex'
            root.mkdir(mode=0o755)
            home = root / 'home'; home.mkdir(mode=0o700)
            auth = home / 'auth.json'; auth.write_text('synthetic cached login'); auth.chmod(0o600)
            with patch.dict(os.environ, {'RADIUS_CODEX_STATE_DIR': str(root)}):
                entrypoint._prepare_codex_state()
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((auth.stat().st_uid, auth.stat().st_gid), (10001, 10001))
            result = subprocess.run([os.path.realpath(sys.executable), '-c', '''
import pathlib, sys
root = pathlib.Path(sys.argv[1])
root.chmod(0o700)
assert (root/'home/auth.json').read_text() == 'synthetic cached login'
(root/'home/auth.json').write_text('refreshed synthetic login')
(root/'runtime.lock').touch()
(root/'login.json').write_text('{}')
''', str(root)], preexec_fn=entrypoint._drop_privileges, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_links_in_state_tree_without_changing_the_target(self):
        with tempfile.TemporaryDirectory(prefix='radius-volume-') as folder:
            root = Path(folder) / 'codex'; root.mkdir()
            outside = Path(folder) / 'outside'; outside.write_text('unchanged')
            (root / 'auth.json').symlink_to(outside)
            original = outside.stat()
            with patch.dict(os.environ, {'RADIUS_CODEX_STATE_DIR': str(root)}):
                with self.assertRaises(RuntimeError): entrypoint._prepare_codex_state()
            self.assertEqual(outside.stat().st_uid, original.st_uid)
            self.assertEqual(outside.read_text(), 'unchanged')

    @unittest.skipUnless(os.geteuid() == 0, 'Production ownership repair requires root')
    def test_native_temporary_helper_links_survive_repeated_startup_without_touching_targets(self):
        with tempfile.TemporaryDirectory(prefix='radius-volume-') as folder:
            root = Path(folder) / 'codex'
            helpers = root / 'home/tmp/arg0-linux/codex-arg0-example'
            helpers.mkdir(parents=True)
            outside = Path(folder) / 'outside'; outside.mkdir()
            executable = outside / 'executable'; executable.write_text('synthetic runtime')
            aliases = [helpers / name for name in ('apply_patch', 'applypatch', 'codex-linux-sandbox')]
            for alias in aliases:
                alias.symlink_to(executable)
            (helpers / 'external-directory').symlink_to(outside, target_is_directory=True)
            original = executable.stat()
            with patch.dict(os.environ, {'RADIUS_CODEX_STATE_DIR': str(root)}):
                entrypoint._prepare_codex_state()
                entrypoint._prepare_codex_state()
            self.assertTrue(all(alias.is_symlink() for alias in aliases))
            self.assertEqual(helpers.stat().st_uid, entrypoint.APP_UID)
            self.assertEqual(executable.stat().st_uid, original.st_uid)
            self.assertEqual(executable.stat().st_gid, original.st_gid)
            self.assertEqual(executable.stat().st_mode, original.st_mode)
            self.assertEqual(executable.read_text(), 'synthetic runtime')

    def test_persistent_state_and_temporary_cache_roots_cannot_be_links(self):
        for relative in ('home', 'home/auth.json', 'home/tmp', 'runtime.lock'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(prefix='radius-volume-') as folder:
                root = Path(folder) / 'codex'; root.mkdir()
                outside = Path(folder) / 'outside'; outside.mkdir()
                link = root / relative; link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside, target_is_directory=True)
                with patch.dict(os.environ, {'RADIUS_CODEX_STATE_DIR': str(root)}):
                    with self.assertRaises(RuntimeError):
                        entrypoint._prepare_codex_state()


if __name__ == '__main__': unittest.main()
