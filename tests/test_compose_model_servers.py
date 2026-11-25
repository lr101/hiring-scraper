import re
import unittest
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "compose.yaml"


class ModelServerCommandTests(unittest.TestCase):
    def test_llama_server_flags_and_values_are_separate_arguments(self):
        # llama-server rejects "--flag=value" ("error: invalid argument: --host=0.0.0.0").
        services = yaml.safe_load(COMPOSE.read_text())["services"]
        for name in ("llm", "llm-reranker"):
            command = services[name]["command"]
            with self.subTest(service=name):
                self.assertTrue(all(isinstance(arg, str) for arg in command))
                self.assertFalse([arg for arg in command if re.match(r"^--[a-z-]+=", arg)])
                self.assertEqual(command[command.index("--host") + 1], "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
