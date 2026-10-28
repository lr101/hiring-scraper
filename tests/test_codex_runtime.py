import importlib
import importlib.util
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import shutil
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from codex_fixture import write_codex_fixture


class CodexRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='radius-codex-test-')
        self.root = Path(self.folder.name)
        self.executable = write_codex_fixture(self.root)
        self.env = patch.dict(os.environ, {'RADIUS_CODEX_STATE_DIR': str(self.root),
            'RADIUS_CODEX_BIN': str(self.executable), 'OPENAI_API_KEY': 'must-not-be-used',
            'CODEX_API_KEY': 'must-not-be-used', 'CODEX_ACCESS_TOKEN': 'must-not-be-used',
            'OPENAI_BASE_URL': 'https://untrusted.invalid', 'T3CODE_HOST': 'must-not-be-inherited'})
        self.env.start()

    def tearDown(self):
        # Unblock any pending test login before deleting its private directory.
        (self.root/'allow-login').touch()
        time.sleep(.08)
        self.env.stop(); self.folder.cleanup()

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.codex_runtime'),
            'Subscription preparation needs an isolated Codex runtime')
        return importlib.import_module('hiring_scraper.app.codex_runtime')

    def connect(self, mode='chatgpt'):
        home=self.root/'home'; home.mkdir(exist_ok=True)
        (home/'auth.json').write_text(json.dumps({'auth_mode':mode}))

    def test_api_key_environment_cannot_enable_subscription_preparation(self):
        runtime=self.module()
        self.assertFalse(runtime.status()['enabled'])
        self.assertTrue(runtime.status()['available'])
        self.connect('apikey')
        self.assertFalse(runtime.status()['enabled'])
        with self.assertRaises(HTTPException) as raised:
            runtime.generate('My CV', {'type':'object'})
        self.assertEqual(raised.exception.status_code,401)

    def test_generation_uses_saved_subscription_and_no_inherited_secrets_or_tools(self):
        runtime=self.module(); self.connect()
        schema={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}
        self.assertEqual(runtime.generate('Private CV text',schema),{'answer':'prepared'})
        seen=json.loads((self.root/'seen.json').read_text())
        self.assertEqual(seen['prompt'],'Private CV text')
        self.assertNotIn('Private CV text',json.dumps(seen['args']))
        self.assertEqual(seen['schema'],schema)
        for key in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_BASE_URL','T3CODE_HOST'):
            self.assertNotIn(key,seen['env'])
        self.assertEqual(seen['env']['CODEX_HOME'],str(self.root/'home'))
        self.assertNotEqual(seen['cwd'],str(Path.cwd()))
        self.assertIn('--ignore-user-config',seen['args'])
        self.assertIn('--ephemeral',seen['args'])
        self.assertIn('forced_login_method="chatgpt"',seen['args'])
        self.assertIn('features.shell_tool=false',seen['args'])
        self.assertIn('features.unified_exec=false',seen['args'])
        self.assertIn('features.multi_agent=false',seen['args'])
        self.assertEqual(seen['args'][seen['args'].index('--sandbox')+1],'read-only')
        self.assertFalse(Path(seen['cwd']).exists(),'Transient prompt/output workspace must be removed')

    def test_device_sign_in_is_pollable_idempotent_and_never_returns_credentials(self):
        runtime=self.module()
        runtime.start_login()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            status=runtime.status()
            if status.get('user_code'):break
            time.sleep(.03)
        self.assertEqual(status['user_code'],'ABCD-EFGH')
        self.assertEqual(status['verification_url'],'https://auth.openai.com/codex/device')
        self.assertEqual(runtime.start_login()['user_code'],'ABCD-EFGH')
        self.assertFalse(status['enabled'])
        (self.root/'allow-login').touch()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and not runtime.status()['enabled']:time.sleep(.03)
        self.assertTrue(runtime.status()['enabled'])
        self.assertNotIn('user_code',runtime.status())
        self.assertEqual((self.root/'home').stat().st_mode&0o777,0o700)
        self.assertNotIn('access_token',json.dumps(runtime.status()))

    def test_disconnect_clears_the_subscription_login(self):
        runtime=self.module(); self.connect()
        self.assertTrue(runtime.status()['enabled'])
        runtime.logout()
        self.assertFalse(runtime.status()['enabled'])
        self.assertFalse((self.root/'home/auth.json').exists())

    def test_expired_sign_in_releases_the_runtime_for_a_new_attempt(self):
        runtime=self.module()
        with patch.object(runtime,'LOGIN_SECONDS',.2):
            runtime.start_login()
            deadline=time.monotonic()+3
            while time.monotonic()<deadline and runtime.status()['login_state']!='expired':time.sleep(.03)
        self.assertEqual(runtime.status()['login_state'],'expired')
        (self.root/'allow-login').touch()
        runtime.start_login()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and not runtime.status()['enabled']:time.sleep(.03)
        self.assertTrue(runtime.status()['enabled'])

    def test_process_errors_are_safe_and_no_api_key_retry_occurs(self):
        runtime=self.module(); self.connect()
        (self.root/'fixture.json').write_text(json.dumps({'fail':True}))
        with self.assertRaises(HTTPException) as raised:runtime.generate('CV',{'type':'object'})
        self.assertEqual(raised.exception.status_code,502)
        self.assertNotIn('private-token',raised.exception.detail)
        self.assertIn('ChatGPT',raised.exception.detail)

    def test_workflow_timeout_kills_the_child_and_releases_the_lease(self):
        runtime=self.module(); self.connect()
        (self.root/'fixture.json').write_text(json.dumps({'delay':10}))
        with self.assertRaises(HTTPException) as raised:
            runtime.generate('CV', {'type':'object'}, timeout=.1)
        self.assertEqual(raised.exception.status_code,504)
        (self.root/'fixture.json').unlink()
        self.assertEqual(runtime.generate('CV', {'type':'object'}), {'answer':'prepared'})

    def test_missing_runtime_is_actionable_and_does_not_start_login(self):
        runtime=self.module()
        with patch.dict(os.environ,{'RADIUS_CODEX_BIN':'/nonexistent/radius/codex'}):
            self.assertFalse(runtime.status()['available'])
            with self.assertRaises(HTTPException) as raised:runtime.start_login()
        self.assertEqual(raised.exception.status_code,503)

    def test_native_runtime_cannot_advertise_shell_files_browser_or_agent_tools(self):
        native = shutil.which('codex')
        if not native: self.skipTest('Pinned Codex binary is not installed')
        runtime = self.module()
        class Handler(BaseHTTPRequestHandler):
            captured = None
            def do_POST(self):
                Handler.captured = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(400); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(b'{"error":{"message":"Controlled offline stop","type":"invalid_request_error"}}')
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        original = runtime._command
        def offline(subcommand):
            return [value.replace('model_provider="openai"','model_provider="offline"') for value in original(subcommand)] + [
                '-c','model_providers.offline.name="Offline test"',
                '-c',f'model_providers.offline.base_url="http://127.0.0.1:{server.server_port}/v1"',
                '-c','model_providers.offline.wire_api="responses"',
                '-c','model_providers.offline.requires_openai_auth=false',
                '-c','model_providers.offline.supports_websockets=false']
        try:
            with patch.dict(os.environ,{'RADIUS_CODEX_BIN':native,'APPLICATION_AI_MODEL':'gpt-5.4'}), \
                    patch.object(runtime,'_connected',return_value=True), patch.object(runtime,'_command',side_effect=offline), \
                    patch.object(runtime,'GENERATION_SECONDS',15):
                with self.assertRaises(HTTPException): runtime.generate('Return JSON only.',{'type':'object'})
            self.assertIsNotNone(Handler.captured, 'The native CLI must use the explicitly configured transport')
            self.assertLessEqual({tool.get('name',tool.get('type')) for tool in Handler.captured.get('tools',[])}, {'request_user_input'})
        finally:
            server.shutdown(); server.server_close()


if __name__=='__main__':unittest.main()

class CancellationTests(CodexRuntimeTests):
    def test_cancel_stops_running_generation(self):
        runtime = self.module(); self.connect()
        (self.root/'fixture.json').write_text(json.dumps({'delay':10}))
        started = time.monotonic()
        with self.assertRaises(HTTPException) as raised:
            runtime.generate('CV', {'type':'object'}, timeout=15, cancelled=lambda: time.monotonic() - started > .2)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertLess(time.monotonic() - started, 4)
        (self.root/'fixture.json').unlink()
        self.assertEqual(runtime.generate('CV', {'type':'object'}), {'answer':'prepared'})
