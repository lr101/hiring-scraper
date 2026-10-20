"""Opt-in private CV trial; the user's source stays outside version control."""
import os
from pathlib import Path
import unittest
from playwright.sync_api import expect
from .test_extension import ExtensionHarness


@unittest.skipUnless(os.environ.get('APPLICATION_TEST_CV_PATH'), 'Set APPLICATION_TEST_CV_PATH to the private marked trial CV')
class AttachedCVTrial(ExtensionHarness):
    def test_real_cv_history_extraction_and_responsibility_fill(self):
        source = Path(os.environ['APPLICATION_TEST_CV_PATH']).read_text()
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#cv_text').fill(source)
        options.get_by_role('button', name='Save profile', exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        saved = self.worker.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(len(saved['experiences']), 4)
        self.assertEqual(len(saved['education']), 2)
        self.assertIn('70%', saved['experiences'][0]['description'])
        self.assertIn('Figma', saved['experiences'][1]['description'])
        self.assertEqual([row['start'] for row in saved['experiences']], ['2023', '2022', '2020', '2017'])
        url = 'http://127.0.0.1:43199/private-trial'
        rows = ''.join(f'<fieldset><legend>Work experience {i+1}</legend><label>Employer<input id=company-{i}></label><label>Job title<input id=title-{i}></label><label>Responsibilities<textarea id=description-{i}></textarea></label><label>Start date<input id=start-{i}></label><label>End date<input id=end-{i}></label></fieldset>' for i in range(4))
        self.context.route(url, lambda route: route.fulfill(content_type='text/html', body=f'<html lang=en><form><label>Email<input type=email></label>{rows}</form></html>'))
        self.page.goto(url)
        popup = self.popup()
        self.fill(popup)
        self.assertIn('70%', self.page.locator('#description-0').input_value())
        self.assertIn('Figma', self.page.locator('#description-1').input_value())
        self.assertEqual(self.page.locator('#start-0').input_value(), '2023')
        self.assertEqual(self.page.locator('#end-0').input_value(), '2025')
        self.assertEqual(self.page.locator('#start-3').input_value(), '2017')
        for i in range(4):
            self.assertTrue(self.page.locator(f'#company-{i}').input_value())
            self.assertTrue(self.page.locator(f'#title-{i}').input_value())
