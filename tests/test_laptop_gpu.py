"""Laptop GPU: routing to the laptop while it is online, parallel slots, setup script."""
import json
import os
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from hiring_scraper.app import application_generation, laptop_gpu, local_llm
from hiring_scraper.app import job_analysis_worker as worker
from hiring_scraper.job_analysis import LLM_MODEL_KEY
from hiring_scraper.matching_v3.evidence import RERANK_MODEL_KEY

ROOT = Path(__file__).resolve().parents[1]


def fake_server(alias: str, slots: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = {'/health': {'status': 'ok'},
                    '/props': {'model_alias': alias, 'total_slots': slots}}.get(self.path)
            self.send_response(200 if body else 404)
            self.end_headers()
            self.wfile.write(json.dumps(body or {}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class LaptopGpuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {'RADIUS_CODEX_STATE_DIR': self.tmp.name,
                                               'JOB_ANALYSIS_LLM_URL': 'http://llm:8080',
                                               'JOB_ANALYSIS_RERANK_URL': 'http://llm-reranker:8080'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        laptop_gpu._probes.clear()
        self.addCleanup(laptop_gpu._probes.clear)

    def test_address_accepts_vpn_ips_and_host_names_only(self):
        self.assertEqual(laptop_gpu.normalise_address(' 100.64.0.7 '), '100.64.0.7')
        self.assertEqual(laptop_gpu.normalise_address('[fd7a:115c::1]'), 'fd7a:115c::1')
        self.assertEqual(laptop_gpu.normalise_address('Laptop.tail1234.ts.net'), 'laptop.tail1234.ts.net')
        for bad in ('http://100.64.0.7', '100.64.0.7:8091', "laptop'; rm -rf ~", 'a b', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                laptop_gpu.normalise_address(bad)
        with self.assertRaises(ValueError):
            laptop_gpu.save(True, '')

    def test_calls_use_the_laptop_only_while_enabled_and_online(self):
        self.assertEqual(laptop_gpu.endpoint('llm'), ('http://llm:8080', 1))
        laptop_gpu.save(False, '100.64.0.7')
        with mock.patch.object(laptop_gpu, '_probe', return_value=3) as probe:
            self.assertEqual(local_llm.llm_url(), 'http://llm:8080')
            probe.assert_not_called()  # Switched off: the laptop is never contacted.
            laptop_gpu.save(True, '100.64.0.7')
            self.assertEqual(laptop_gpu.endpoint('llm'), ('http://100.64.0.7:8091', 3))
            self.assertEqual(laptop_gpu.endpoint('reranker'), ('http://100.64.0.7:8092', 3))
            self.assertTrue(laptop_gpu.serving())
        with mock.patch.object(laptop_gpu, '_probe', return_value=None):
            laptop_gpu._probes.clear()
            self.assertEqual(laptop_gpu.endpoint('llm'), ('http://llm:8080', 1))
            self.assertFalse(laptop_gpu.serving())

    def test_parallel_requests_are_capped(self):
        laptop_gpu.save(True, 'laptop')
        with mock.patch.object(laptop_gpu, '_probe', return_value=16):
            self.assertEqual(local_llm.llm_parallel(), laptop_gpu.MAX_PARALLEL)

    def test_failed_laptop_call_switches_to_the_homelab_until_the_next_probe(self):
        laptop_gpu.save(True, '100.64.0.7')
        with mock.patch.object(laptop_gpu, '_probe', return_value=2), \
                mock.patch.object(local_llm, '_send', side_effect=OSError('unreachable')):
            with self.assertRaises(OSError):
                local_llm.rerank('query', ['document'])
            self.assertEqual(local_llm.rerank_url(), 'http://llm-reranker:8080')
            self.assertEqual(local_llm.llm_url(), 'http://100.64.0.7:8091')  # The LLM kept working.

    def test_probe_requires_the_pinned_model(self):
        right, wrong = fake_server(LLM_MODEL_KEY, 2), fake_server('some/other-model', 4)
        for server in (right, wrong):
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.assertEqual(laptop_gpu._probe(f'http://127.0.0.1:{right.server_port}', LLM_MODEL_KEY), 2)
        self.assertIsNone(laptop_gpu._probe(f'http://127.0.0.1:{wrong.server_port}', LLM_MODEL_KEY))
        self.assertIsNone(laptop_gpu._probe('http://127.0.0.1:9', LLM_MODEL_KEY))

    def test_status_reports_each_laptop_server(self):
        self.assertEqual(laptop_gpu.status(), {'enabled': False, 'address': '', 'llm': None, 'reranker': None})
        laptop_gpu.save(True, '100.64.0.7')
        with mock.patch.object(laptop_gpu, '_probe', side_effect=lambda url, key: 2 if url.endswith('8091') else None):
            status = laptop_gpu.status()
        self.assertEqual(status['llm'], {'online': True, 'slots': 2})
        self.assertEqual(status['reranker'], {'online': False, 'slots': 0})

    def test_setup_script_has_the_saved_address_and_the_pinned_models(self):
        laptop_gpu.save(False, '100.64.0.7')
        response = application_generation.laptop_setup_script()
        script = response.body.decode()
        self.assertIn("CONFIGURED_ADDRESS='100.64.0.7'", script)
        self.assertIn('attachment', response.headers['content-disposition'])
        self.assertIn(f"LLM_ALIAS='{LLM_MODEL_KEY}'", script)
        self.assertIn(f"RERANK_ALIAS='{RERANK_MODEL_KEY}'", script)
        compose = (ROOT / 'compose.yaml').read_text()
        dev = (ROOT / 'scripts/dev/local-models.sh').read_text()
        for name in ('LLM_URL', 'RERANK_URL'):
            url = re.search(rf'^{name}=(\S+)$', script, re.M).group(1)
            self.assertIn(url, compose)
        self.assertEqual(re.search(r'^LLAMA_BUILD=(\S+)$', script, re.M).group(1),
                         re.search(r'^LLAMA_BUILD=(\S+)$', dev, re.M).group(1))
        self.assertIn(re.search(r'^LLAMA_BUILD=(\S+)$', script, re.M).group(1), compose)


class ParallelRunTests(unittest.TestCase):
    def test_one_outage_keeps_the_other_results(self):
        def step(item):
            if item == 2:
                raise OSError('laptop gone')
            return item
        self.assertEqual(sorted(worker._run_parallel(step, [1, 2, 3], 3)), [1, 3])
        with self.assertRaises(OSError):
            worker._run_parallel(lambda item: step(2), [1, 2], 2)

    def test_items_run_at_the_same_time(self):
        barrier = threading.Barrier(3, timeout=5)
        self.assertEqual(worker._run_parallel(lambda item: barrier.wait() is not None and item, [1, 2, 3], 3),
                         [1, 2, 3])

    def test_injected_models_and_homelab_run_one_at_a_time(self):
        with mock.patch.object(worker, 'llm_parallel', return_value=3), \
                mock.patch.object(worker, 'rerank_parallel', return_value=3), \
                mock.patch.object(worker, '_luna_selected', return_value=False):
            self.assertEqual(worker.analysis_parallel(), 3)
            self.assertEqual(worker.evidence_parallel(), 3)
            self.assertEqual(worker.analysis_parallel(analyse=lambda lines: ''), 1)
        with mock.patch.object(worker, 'llm_parallel', return_value=3), \
                mock.patch.object(worker, '_luna_selected', return_value=True):
            self.assertEqual(worker.analysis_parallel(), 1)

    def test_evidence_runs_in_one_thread_at_a_time(self):
        with worker._EVIDENCE_LOCK:
            self.assertFalse(worker._exclusive(lambda: True))
        self.assertTrue(worker._exclusive(lambda: True))


if __name__ == '__main__':
    unittest.main()
