from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile


class LegacyApplicationDraftTests(unittest.TestCase):
    def test_export_and_delete_are_scoped_to_the_requested_profile(self):
        from hiring_scraper.app import legacy_application_drafts as legacy

        with TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / 'application-jobs'
            revisions = root / 'document-revisions' / 'application-jobs'
            jobs.mkdir()
            revisions.mkdir(parents=True)
            (jobs / '1-2.draft.json').write_text('{"draft":"Fictional applicant edit"}')
            (revisions / '1-2.json').write_text('{"revision":"Fictional suggestion"}')
            other = jobs / '11-2.draft.json'
            other.write_text('{"draft":"Fictional second profile"}')
            linked = jobs / '1-3.draft.json'
            linked.symlink_to(other)

            output = BytesIO()
            legacy.export_profile(root, 1, output)
            with ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {
                    'application-jobs/1-2.draft.json', 'document-revisions/application-jobs/1-2.json',
                })
                self.assertIn(b'Fictional applicant edit', archive.read('application-jobs/1-2.draft.json'))

            legacy.delete_profile(root, 1)
            self.assertEqual(list(revisions.iterdir()), [])
            self.assertEqual(list(jobs.iterdir()), [other])
            self.assertTrue(other.is_file())
