import unittest
from unittest import mock

from hiring_scraper import html_jobs
from hiring_scraper.postings import merge_duplicate_postings
from hiring_scraper.regional import normalize_job


class DuplicatePostingTests(unittest.TestCase):
    def test_same_id_rows_merge_instead_of_overwriting(self):
        structured = {'id': 'R1', 'title': 'Analyst', 'url': 'https://x.example/job/R1',
                      'description': '## Tasks\n- Build', 'locations': [{'label': 'London', 'country_code': 'GB'}],
                      'location': 'London', 'raw_metadata': {'extraction_method': 'schema_org_jobposting'}}
        scraped = {'id': 'R1', 'title': 'Analyst', 'url': 'https://x.example/job/R1',
                   'description': 'Share Job Post Tasks Build and much more page chrome text', 'locations': [],
                   'employment_type': 'full-time', 'raw_metadata': {'extraction_method': 'html_job_detail'}}
        other = {'id': 'R2', 'title': 'Engineer', 'url': 'https://x.example/job/R2'}
        rows = merge_duplicate_postings([scraped, other, structured, {'title': 'No id'}])
        self.assertEqual([row.get('id') for row in rows], ['R1', 'R2', None])
        merged = rows[0]
        self.assertEqual(merged['locations'], [{'label': 'London', 'country_code': 'GB'}])
        self.assertEqual(merged['description'], '## Tasks\n- Build')
        self.assertEqual(merged['employment_type'], 'full-time')
        self.assertEqual(merged['raw_metadata']['extraction_method'], 'schema_org_jobposting')
        self.assertEqual(merged['raw_metadata']['merged_source_rows'], 2)

    def test_a_failing_strategy_keeps_other_jobs_and_marks_the_page_incomplete(self):
        body = ('<h2>Open positions</h2><ul><li><a href="/jobs/data-analyst">Data Analyst</a> '
                '<span class="location">Berlin</span><p>Build analytics workflows for our team.</p></li></ul>')
        with mock.patch.object(html_jobs, '_structured_job_cards', side_effect=RuntimeError('boom')):
            result = html_jobs.extract_html_jobs(body, 'https://acme.example/careers/')
        self.assertEqual([job['title'] for job in result['jobs']], ['Data Analyst'])
        self.assertEqual(result['strategy_errors'], ['job_cards: RuntimeError'])
        self.assertFalse(result['complete'])


class ArbeitsagenturLocationTests(unittest.TestCase):
    ROW = {'refnr': '10000-1', 'titel': 'Analyst (m/w/d)', 'arbeitgeber': 'ACME GmbH'}

    def test_search_town_beats_a_country_only_detail_location(self):
        job = normalize_job({**self.ROW, 'stellenlokationen': [{'adresse': {'land': 'DEUTSCHLAND'}}],
                             'arbeitsort': {'ort': 'Mannheim', 'plz': '68159', 'land': 'Deutschland'}})
        self.assertEqual([item['label'] for item in job['locations']], ['Mannheim, 68159'])

    def test_region_names_a_job_without_town(self):
        job = normalize_job({**self.ROW, 'arbeitsort': {'region': 'Baden-Württemberg', 'land': 'DEUTSCHLAND'}})
        self.assertEqual(job['locations'][0]['label'], 'Baden-Württemberg, Deutschland')
        self.assertEqual(job['locations'][0]['country_code'], 'DE')


if __name__ == '__main__':
    unittest.main()
