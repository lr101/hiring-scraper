"""Production JSON-RPC quota probing against an isolated subprocess fixture."""
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from codex_fixture import write_codex_fixture


class CodexUsageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='radius-quota-test-')
        self.root = Path(self.folder.name)
        self.env = patch.dict(os.environ, {
            'RADIUS_CODEX_STATE_DIR': str(self.root),
            'RADIUS_CODEX_BIN': str(write_codex_fixture(self.root)),
            'OPENAI_API_KEY': 'private-key', 'CODEX_API_KEY': 'private-key',
            'CODEX_ACCESS_TOKEN': 'private-token', 'OPENAI_BASE_URL': 'https://untrusted.invalid',
            'CODEX_HOME': '/developer/auth', 'APPLICATION_AI_MODEL': 'document-model',
        })
        self.env.start()
        self.runtime = importlib.import_module('hiring_scraper.app.codex_runtime')
        home = self.root / 'home'; home.mkdir()
        (home / 'auth.json').write_text(json.dumps({'auth_mode': 'chatgpt'}))

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def configure(self, **settings):
        (self.root / 'fixture.json').write_text(json.dumps(settings))

    def window(self, used=49, minutes=300, reset=None):
        return {'usedPercent': used, 'windowDurationMins': minutes,
                'resetsAt': int(time.time()) + 3600 if reset is None else reset}

    def payload(self, primary=None, secondary=None, **extra):
        return {'rateLimits': {'limitId': 'codex', 'primary': primary or self.window(),
                'secondary': secondary, 'credits': None, 'planType': 'plus'}, **extra}

    def usage(self):
        self.assertTrue(hasattr(self.runtime, 'subscription_usage'), 'Public safe quota probe is missing')
        return self.runtime.subscription_usage()

    @contextmanager
    def started_probe(self, *, child=False):
        # Time only the controlled stall after the fixture has started; heavily
        # loaded CI hosts can spend longer than the probe timeout spawning Python.
        original = self.runtime.subprocess.Popen
        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            ready = self.root / ('usage-child.pid' if child else 'app-server.pid')
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if ready.exists():
                    return process
                time.sleep(.01)
            self.runtime._stop(process)
            self.fail('Quota fixture did not start')
        with patch.object(self.runtime.subprocess, 'Popen', side_effect=launch):
            yield

    def test_reads_subscription_five_hour_window_with_handshake_and_isolated_auth(self):
        usage = self.usage()
        self.assertEqual({key: usage[key] for key in ('available', 'allowed', 'reason', 'used_percent', 'window_minutes', 'limit_percent')},
            {'available': True, 'allowed': True, 'reason': 'ready', 'used_percent': 49, 'window_minutes': 300, 'limit_percent': 50})
        seen = json.loads((self.root / 'usage-seen.json').read_text())
        self.assertEqual([message['method'] for message in seen['messages']],
                         ['initialize', 'initialized', 'account/read', 'account/rateLimits/read'])
        self.assertEqual(seen['env']['CODEX_HOME'], str(self.root / 'home'))
        for feature in ('hooks', 'plugins', 'shell_tool', 'multi_agent'):
            self.assertIn('features.' + feature + '=false', seen['args'])
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'OPENAI_BASE_URL'):
            self.assertNotIn(key, seen['env'])
        self.assertFalse((self.root / 'seen.json').exists(), 'Quota reads must not launch inference')
        self.assertNotIn('private@', json.dumps(usage))

    def test_accepts_secondary_five_hour_window_and_never_substitutes_other_windows(self):
        self.configure(quota_payload=self.payload(self.window(2, 15), self.window(45)))
        self.assertEqual(self.usage()['used_percent'], 45)
        for minutes in (15, 60, 299, 301, 10080):
            with self.subTest(minutes=minutes):
                self.configure(quota_payload=self.payload(self.window(2, minutes)))
                usage = self.usage()
                self.assertFalse(usage['allowed'])
                self.assertEqual(usage['reason'], 'five_hour_window_unavailable')
                self.assertIsNone(usage['used_percent'])

    def test_missing_malformed_expired_and_nonfinite_usage_fail_closed(self):
        for malformed in (None, {}, {'rateLimits': None}, self.payload(self.window('49')),
                          self.payload(self.window(float('nan'))), self.payload(self.window(101)),
                          self.payload(self.window(-1)), self.payload(self.window(reset=1)),
                          self.payload(self.window(reset='tomorrow'))):
            with self.subTest(payload=malformed):
                self.configure(quota_payload=malformed)
                self.assertFalse(self.usage()['allowed'])

    def test_shared_and_explicit_luna_limits_are_conservative_and_unrelated_models_ignored(self):
        buckets = {'codex': {'primary': self.window(30), 'secondary': None},
                   'gpt-6-luna': {'primary': self.window(52), 'secondary': None},
                   'gpt-6-astra': {'primary': self.window(99), 'secondary': None}}
        self.configure(quota_payload={'rateLimits': buckets['codex'], 'rateLimitsByLimitId': buckets})
        usage = self.usage()
        self.assertEqual(usage['used_percent'], 52)
        self.assertEqual(usage['reason'], 'usage_limit_reached')
        buckets['gpt-6-luna']['primary']['usedPercent'] = 40
        self.configure(quota_payload={'rateLimits': buckets['codex'], 'rateLimitsByLimitId': buckets})
        self.assertEqual(self.usage()['used_percent'], 40)
        self.assertTrue(self.usage()['allowed'])
        self.configure(quota_payload={'rateLimitsByLimitId': {'gpt-6-astra': buckets['gpt-6-astra']}})
        self.assertFalse(self.usage()['allowed'])

    def test_shared_five_hour_window_can_coexist_with_luna_longer_window(self):
        buckets = {'codex': {'primary': self.window(49)},
                   'gpt-6-luna': {'primary': self.window(10, 10080)}}
        self.configure(quota_payload={'rateLimitsByLimitId': buckets})
        self.assertEqual(self.usage()['used_percent'], 49)
        self.assertTrue(self.usage()['allowed'])
        buckets['gpt-6-luna']['primary']['usedPercent'] = 100
        self.configure(quota_payload={'rateLimitsByLimitId': buckets})
        self.assertEqual(self.usage()['reason'], 'usage_limit_reached')

    def test_present_luna_bucket_without_windows_does_not_grant_access(self):
        for luna in ({}, {'primary': None, 'secondary': None}):
            with self.subTest(luna=luna):
                buckets = {'codex': {'primary': self.window(49)}, 'gpt-6-luna': luna}
                self.configure(quota_payload={'rateLimitsByLimitId': buckets})
                usage = self.usage()
                self.assertFalse(usage['allowed'])
                self.assertEqual(usage['reason'], 'usage_unavailable')

    def test_public_usage_read_respects_the_shared_runtime_lease(self):
        with self.runtime._lock(self.root):
            usage = self.usage()
        self.assertFalse(usage['allowed'])
        self.assertEqual(usage['reason'], 'usage_unavailable')
        self.assertFalse((self.root / 'usage-seen.json').exists())

    def test_known_exhausted_longer_window_blocks_even_when_five_hour_is_low(self):
        self.configure(quota_payload=self.payload(self.window(10), self.window(100, 10080)))
        self.assertEqual(self.usage()['reason'], 'usage_limit_reached')
        self.assertFalse(self.usage()['allowed'])

    def test_bad_or_api_key_auth_never_launches_inference(self):
        for mode in ('apikey', 'missing'):
            (self.root / 'home/auth.json').write_text(json.dumps({'auth_mode': mode}))
            self.assertEqual(self.usage()['reason'], 'disconnected')
            with self.assertRaises(HTTPException) as raised:
                self.runtime.generate('Rank', {}, model='gpt-6-luna', reasoning_effort='medium', usage_limit_percent=50)
            self.assertIn(raised.exception.status_code, (401, 429))
            self.assertFalse((self.root / 'seen.json').exists())
        (self.root / 'home/auth.json').write_text(json.dumps({'auth_mode': 'chatgpt'}))
        self.configure(account={'type': 'apiKey'})
        self.assertEqual(self.usage()['reason'], 'disconnected')

    def test_guard_blocks_at_fifty_without_exec_and_allows_fortynine_with_requested_options(self):
        for used in (50, 75):
            self.configure(quota_payload=self.payload(self.window(used)))
            with self.assertRaises(HTTPException) as raised:
                self.runtime.generate('Rank', {}, model='gpt-6-luna', reasoning_effort='medium', usage_limit_percent=50)
            self.assertEqual(raised.exception.status_code, 429)
            self.assertFalse((self.root / 'seen.json').exists())
        self.configure(quota_payload=self.payload())
        self.assertEqual(self.runtime.generate('Rank', {}, model='gpt-6-luna', reasoning_effort='medium', usage_limit_percent=50), {'answer': 'prepared'})
        seen = json.loads((self.root / 'seen.json').read_text())
        self.assertEqual(seen['args'][seen['args'].index('--model') + 1], 'gpt-6-luna')
        self.assertIn('model_reasoning_effort="medium"', seen['args'])

    def test_guard_rechecks_current_usage_and_uses_existing_runtime_lock(self):
        self.assertTrue(self.usage()['allowed'])
        self.configure(quota_payload=self.payload(self.window(51)))
        with self.assertRaises(HTTPException) as raised:
            self.runtime.generate('Rank', {}, usage_limit_percent=50)
        self.assertEqual(raised.exception.status_code, 429)
        with self.runtime._lock(self.root):
            with self.assertRaises(HTTPException) as raised:
                self.runtime.generate('Rank', {}, usage_limit_percent=50)
            self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse((self.root / 'seen.json').exists())

    def test_protocol_error_and_response_size_fail_safely(self):
        for settings in ({'usage_error': True}, {'usage_bytes': 200000},
                         {'usage_raw': '[' * 1100 + ']' * 1100}):
            self.configure(**settings)
            usage = self.usage()
            self.assertFalse(usage['allowed'])
            self.assertEqual(usage['reason'], 'usage_unavailable')
            self.assertNotIn('private-token', json.dumps(usage))

    def test_unknown_bucket_and_malformed_luna_bucket_do_not_grant_access(self):
        for other in ({'codex_other': {'primary': self.window(1)}}, {'gpt-6-luna': None}):
            self.configure(quota_payload=self.payload(rateLimitsByLimitId=other))
            self.assertFalse(self.usage()['allowed'])

    def test_unavailable_quota_and_server_api_key_auth_block_exec(self):
        for settings in ({'usage_error': True}, {'account': {'type': 'apiKey'}}):
            self.configure(**settings)
            with self.assertRaises(HTTPException) as raised:
                self.runtime.generate('Rank', {}, usage_limit_percent=50)
            self.assertEqual(raised.exception.status_code, 429)
            self.assertFalse((self.root / 'seen.json').exists())

    def test_pathological_nonfinite_numeric_value_is_safe(self):
        self.configure(quota_payload=self.payload(self.window(reset=10 ** 400)))
        self.assertFalse(self.usage()['allowed'])

    def test_document_defaults_keep_the_configured_model_without_quota_probe(self):
        self.configure(usage_error=True)
        self.runtime.generate('Documents', {})
        seen = json.loads((self.root / 'seen.json').read_text())
        self.assertEqual(seen['args'][seen['args'].index('--model') + 1], 'document-model')
        self.assertFalse((self.root / 'usage-seen.json').exists())

    def test_usage_cleanup_stops_descendants_even_when_server_already_exited(self):
        self.configure(usage_exit_with_child=True)
        child_pid = None
        try:
            with self.started_probe(child=True), patch.object(self.runtime, 'USAGE_SECONDS', .15):
                self.assertFalse(self.usage()['allowed'])
            child_pid = int((self.root / 'usage-child.pid').read_text())
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                stat = Path(f'/proc/{child_pid}/stat')
                if not stat.exists() or stat.read_text().split()[2] == 'Z':
                    break
                time.sleep(.01)
            else:
                self.fail('Quota process descendants survived cleanup')
        finally:
            if child_pid is not None:
                try: os.kill(child_pid, 9)
                except ProcessLookupError: pass

    def test_usage_timeout_kills_process_and_next_read_succeeds(self):
        self.configure(usage_delay=10)
        with self.started_probe(), patch.object(self.runtime, 'USAGE_SECONDS', .15):
            self.assertFalse(self.usage()['allowed'])
        pid = int((self.root / 'app-server.pid').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.configure()
        self.assertTrue(self.usage()['allowed'])


if __name__ == '__main__':
    unittest.main()
