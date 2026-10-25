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


if __name__ == '__main__': unittest.main()
