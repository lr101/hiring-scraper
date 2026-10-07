from pathlib import Path
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch

from hiring_scraper.provenance import git_metadata


class GitProvenanceTests(unittest.TestCase):
    def test_not_a_checkout_does_not_report_clean(self):
        with patch('hiring_scraper.provenance.subprocess.run',
                   return_value=CompletedProcess([], 128, '', 'not a repository')):
            self.assertEqual(git_metadata(Path('/source')), {'git_revision': None, 'git_dirty': None})

    def test_verified_dirty_checkout_retains_revision(self):
        revision = 'a' * 40
        with patch('hiring_scraper.provenance.subprocess.run', side_effect=[
                CompletedProcess([], 0, revision + '\n', ''),
                CompletedProcess([], 0, ' M source.py\n', '')]) as command:
            self.assertEqual(git_metadata(Path('/source')), {'git_revision': revision, 'git_dirty': True})
            self.assertEqual(command.call_args.kwargs['cwd'], Path('/source'))

    def test_verified_clean_checkout_is_false(self):
        with patch('hiring_scraper.provenance.subprocess.run', side_effect=[
                CompletedProcess([], 0, 'b' * 40, ''), CompletedProcess([], 0, '', '')]):
            self.assertIs(git_metadata(Path('/source'))['git_dirty'], False)

    def test_status_failure_keeps_verified_revision_and_unknown_dirty_state(self):
        with patch('hiring_scraper.provenance.subprocess.run', side_effect=[
                CompletedProcess([], 0, 'c' * 40, ''), FileNotFoundError('git missing')]):
            self.assertEqual(git_metadata(Path('/source')), {'git_revision': 'c' * 40, 'git_dirty': None})
