"""Embedding requests must fit a small native-memory budget under backfill load."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from hiring_scraper.app.semantic_search_worker import LocalE5


class MemoryBudgetSession:
    """Stand in for native inference without allocating transformer attention tensors."""
    def __init__(self):
        self.active = threading.Lock()

    def get_inputs(self):
        return [SimpleNamespace(name='input_ids'), SimpleNamespace(name='attention_mask')]

    def run(self, outputs, inputs):
        if inputs['input_ids'].shape[0] > 4:
            raise MemoryError('Native batch exceeded the memory budget')
        if not self.active.acquire(blocking=False):
            raise MemoryError('Concurrent native runs exceeded the memory budget')
        try:
            time.sleep(0.02)
            ids = inputs['input_ids'].astype(np.float32)
            return [np.stack((ids, np.ones_like(ids)), axis=-1)]
        finally:
            self.active.release()


class InferenceMemoryTests(unittest.TestCase):
    def setUp(self):
        tokenizer = Tokenizer(WordLevel({'[UNK]': 0, '<pad>': 1, 'one': 2, 'two': 3}))
        tokenizer.pre_tokenizer = Whitespace()
        with patch('hiring_scraper.app.semantic_search_worker.Tokenizer.from_file', return_value=tokenizer), \
             patch.object(LocalE5, '_model_file', return_value='unused'), \
             patch('hiring_scraper.app.semantic_search_worker.ort.InferenceSession', return_value=MemoryBudgetSession()):
            self.model = LocalE5()

    def test_large_request_fits_native_budget_and_preserves_vector_order(self):
        vectors = self.model.encode(['one', 'two'] * 16)
        self.assertEqual(len(vectors), 32)
        np.testing.assert_allclose(vectors[0], [2 / np.sqrt(5), 1 / np.sqrt(5)], rtol=1e-6)
        np.testing.assert_allclose(vectors[-1], [3 / np.sqrt(10), 1 / np.sqrt(10)], rtol=1e-6)
        self.assertEqual(self.model.encode([]), [])

    def test_concurrent_requests_fit_one_native_run_budget(self):
        start = threading.Barrier(2)
        def request(text):
            start.wait(timeout=5)
            return self.model.encode([text])[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(request, 'one')
            two = pool.submit(request, 'two')
            results = [one.result(timeout=5), two.result(timeout=5)]
        np.testing.assert_allclose(results, [[2 / np.sqrt(5), 1 / np.sqrt(5)],
                                             [3 / np.sqrt(10), 1 / np.sqrt(10)]], rtol=1e-6)
