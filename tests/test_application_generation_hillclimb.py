"""Contracts for the private, parallel application-generation benchmark runner."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / 'scripts' / 'hillclimb' / 'run_application_generation.py'
SPEC = importlib.util.spec_from_file_location('application_generation_hillclimb_runner', RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class HillclimbScenarioTests(unittest.TestCase):
    def scenario(self, folder):
        cv = Path(folder) / 'base.tex'
        cv.write_text('\\documentclass{article}\n\\begin{document}Applicant\\end{document}\n')
        path = Path(folder) / 'scenario.json'
        path.write_text(json.dumps({
            'version': 1,
            'scenario_id': 'project-manager-role',
            'cv_file': 'base.tex',
            'profile': {'name': 'Applicant', 'summary': 'Project coordination'},
            'job': {
                'title': 'Project manager',
                'company_name': 'Example GmbH',
                'description': 'Coordinate cross-functional projects.',
                'url': 'https://example.invalid/jobs/42',
            },
            'language': 'en',
            'application_notes': '',
        }))
        return path

    def test_scenario_paths_are_resolved_relative_to_the_scenario_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.scenario(folder)
            loaded = runner.load_scenario(path)
            self.assertEqual(loaded.cv_path, Path(folder) / 'base.tex')
            self.assertEqual(loaded.data['job']['title'], 'Project manager')

    def test_each_candidate_run_gets_a_distinct_non_overwritable_result_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            first = runner.result_directory(folder, 'project-manager-role', 'candidate-a', 'run-001')
            second = runner.result_directory(folder, 'project-manager-role', 'candidate-b', 'run-001')
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir())
            with self.assertRaises(FileExistsError):
                runner.result_directory(folder, 'project-manager-role', 'candidate-a', 'run-001')

    def test_run_identifiers_cannot_escape_the_result_root(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                runner.result_directory(folder, 'scenario', '../candidate', 'run-001')

    def test_fixture_revisions_remain_valid_after_database_reload(self):
        from sqlalchemy import create_engine
        from hiring_scraper.app import application_generation as generation
        from test_tex_cv import source

        with tempfile.TemporaryDirectory() as folder:
            path = self.scenario(folder)
            (Path(folder) / 'base.tex').write_text(source())
            engine = create_engine(f'sqlite:///{Path(folder) / "fixture.sqlite3"}')
            session, profile_id, request = runner._build_database(runner.load_scenario(path), engine)
            try:
                session.rollback()
                session.expire_all()
                data, revision = generation._snapshot(profile_id, request, session)
                self.assertEqual(data['job']['title'], 'Project manager')
                self.assertTrue(revision)
            finally:
                session.close()
                engine.dispose()

    def test_blocked_or_unknown_quota_stops_before_fixture_or_generation(self):
        from hiring_scraper.app import codex_runtime
        for quota in (
            {'available': True, 'allowed': False, 'used_percent': 65},
            {'available': False, 'allowed': False, 'used_percent': None},
            {'available': True, 'allowed': True, 'used_percent': 50},
        ):
            with self.subTest(quota=quota), tempfile.TemporaryDirectory() as folder:
                path = self.scenario(folder)
                with patch.object(codex_runtime, 'subscription_usage', return_value=quota), \
                        patch.object(runner, '_build_database') as build, \
                        patch.object(codex_runtime, 'generate') as generate:
                    status, directory = runner.run(path, output_root=Path(folder) / 'results',
                                                   candidate='test', run_id='blocked')
                self.assertEqual(status, 1)
                build.assert_not_called()
                generate.assert_not_called()
                self.assertEqual(json.loads((directory / 'run.json').read_text())['http_status'], 429)

    def test_generation_passes_original_quota_limit_to_provider(self):
        from hiring_scraper.app import application_generation as generation
        with patch.object(generation.codex_runtime, 'generate', return_value={'summary': 'Checked.', 'findings': []}) as provider:
            generation._generate('Synthetic review', generation.FinalContentReview,
                                 time.monotonic() + 10, runner.RunProgress())
        self.assertEqual(provider.call_args.kwargs['usage_limit_percent'], 50)

    def test_run_records_requested_default_separately_from_resolved_model(self):
        from hiring_scraper.app import application_generation as generation
        from test_tex_cv import source
        with tempfile.TemporaryDirectory() as folder:
            path = self.scenario(folder)
            (Path(folder) / 'base.tex').write_text(source())
            response = {'package': {'format': 'radius-application-v1', 'cv': 'CV', 'cover_letter': 'Letter'},
                        'model': 'resolved-default', 'provider': 'test'}
            with patch.object(generation.codex_runtime, 'subscription_usage', return_value={
                    'available': True, 'allowed': True, 'used_percent': 10}), \
                    patch.object(generation, '_prepare_application', return_value=response):
                status, directory = runner.run(path, output_root=Path(folder) / 'results',
                                               candidate='test', run_id='default')
            self.assertEqual(status, 0)
            metadata = json.loads((directory / 'run.json').read_text())
            self.assertIn('requested_model', metadata)
            self.assertIsNone(metadata['requested_model'])
            self.assertEqual(metadata['model'], 'resolved-default')


if __name__ == '__main__':
    unittest.main()
