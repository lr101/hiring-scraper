"""The extension opens Radius preparation for the selected current job."""
import json
import io
import os
import re
from pathlib import Path
from playwright.sync_api import expect
from .test_production_preparation import ProductionHarness
from .test_application_journey import LISTING
from .test_tex_extension import tex_source


class CurrentJobFlowTests(ProductionHarness):
    def test_prepare_action_opens_radius_application_for_review(self):
        upload = self.context.request.put(
            self.origin + '/api/v1/profiles/1/cv?filename=base.tex&language=en',
            data=tex_source().encode(),
            headers={'Content-Type': 'application/x-tex'},
        )
        self.assertTrue(upload.ok, upload.text())
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#radius-origin').fill(self.origin)
        options.locator('#radius-connect').click()
        expect(options.locator('#radius-status')).to_contain_text('1 jobs cached')
        options.close()

        self.page.goto(LISTING)
        popup = self.popup()
        expect(popup.locator('#job-entries')).to_contain_text('Research project manager')
        with self.context.expect_page() as opened:
            popup.get_by_role('button', name='Prepare documents', exact=True).click()

        preparation = opened.value
        preparation.wait_for_url(f'{self.origin}/jobs/1574/apply?profile_id=1')
        expect(preparation.get_by_role('heading', name='Tailor this application.')).to_be_visible()
        expect(preparation.get_by_role('group', name='ChatGPT subscription connection')).to_be_visible()
        expect(preparation.get_by_role('button', name='Continue with ChatGPT')).to_be_enabled()

    def test_base_cv_and_contact_details_can_be_saved_from_extension_settings(self):
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#radius-origin').fill(self.origin)
        options.locator('#radius-connect').click()
        expect(options.locator('#radius-status')).to_contain_text('1 jobs cached')
        options.reload()
        expect(options.locator('#first_name')).to_have_value('')
        options.locator('#first_name').fill('Alex')
        options.get_by_label('Import English .tex file', exact=True).set_input_files({'name':'base.tex','mimeType':'application/x-tex','buffer':tex_source().encode()})
        expect(options.locator('#cv_text')).to_have_value(tex_source())
        options.get_by_role('button',name='Save profile',exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved and synced with Radius.')
        saved = self.context.request.get(self.origin+'/api/v1/profiles').json()['items'][0]
        self.assertEqual(saved['application_details']['first_name'],'Alex')
        self.assertIn('en',saved['cv']['languages'])

    def test_preparation_refreshes_a_cv_replaced_since_connection(self):
        source = tex_source()
        uploaded = self.context.request.put(self.origin+'/api/v1/profiles/1/cv?filename=base.tex&language=en',data=source.encode(),headers={'Content-Type':'application/x-tex'})
        self.assertTrue(uploaded.ok, uploaded.text())
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#radius-origin').fill(self.origin)
        options.locator('#radius-connect').click()
        expect(options.locator('#radius-status')).to_contain_text('1 jobs cached')
        replaced = self.context.request.put(self.origin+'/api/v1/profiles/1/cv?filename=updated.tex&language=en',data=source.replace('work-1','work-new').encode(),headers={'Content-Type':'application/x-tex'})
        self.assertTrue(replaced.ok, replaced.text())
        self.page.goto(LISTING)
        popup = self.popup()
        tab_id = self.worker.evaluate("async url=>(await chrome.tabs.query({})).find(tab=>tab.url===url).id", LISTING)
        response = popup.evaluate("async tabId=>chrome.runtime.sendMessage({type:'prepare-current-job',tabId,jobId:1574})",tab_id)
        self.assertTrue(response['ok'], response)
        state = self.worker.evaluate("async () => chrome.storage.local.get(['radiusVault','applicant'])")
        self.assertEqual(state['applicant']['experiences'][0]['id'],'work-new')
        canonical=self.context.request.get(self.origin+'/api/v1/profiles').json()['items'][0]['cv']['updated_at']
        self.assertEqual(state['radiusVault']['settings']['cv_updated_at'],canonical)

    def test_connected_contact_save_preserves_legacy_history_and_cv_removal_is_explicit(self):
        from PIL import Image
        portrait = io.BytesIO()
        Image.new('RGB', (4, 4), 'red').save(portrait, format='PNG')
        photo = portrait.getvalue()
        uploaded_photo = self.context.request.put(
            self.origin+'/api/v1/profiles/1/photo?filename=portrait.png',
            data=photo, headers={'Content-Type':'image/png'})
        self.assertTrue(uploaded_photo.ok, uploaded_photo.text())
        photo_metadata = uploaded_photo.json()
        history=[dict(id='legacy-1',company='Legacy company',title='Engineer',start='2018',end='2020',current=False)]
        response=self.context.request.put(self.origin+'/api/v1/profiles/1',data={'name':'Research profile','application_details':{'experiences':history}})
        self.assertTrue(response.ok,response.text())
        uploaded=self.context.request.put(self.origin+'/api/v1/profiles/1/cv?filename=base.tex&language=en',data=tex_source().encode(),headers={'Content-Type':'application/x-tex'})
        self.assertTrue(uploaded.ok,uploaded.text())
        options=self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#radius-origin').fill(self.origin)
        options.locator('#radius-connect').click()
        expect(options.locator('#radius-status')).to_contain_text('1 jobs cached')
        options.reload()
        options.locator('#first_name').fill('Edited')
        options.get_by_role('button',name='Save profile',exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved and synced with Radius.')
        saved=self.context.request.get(self.origin+'/api/v1/profiles').json()['items'][0]
        self.assertEqual(saved['application_details']['first_name'], 'Edited')
        self.assertEqual(saved['application_details']['experiences'][0]['id'],'legacy-1')
        self.assertEqual(saved['photo'], photo_metadata)
        self.assertEqual(self.context.request.get(self.origin+'/api/v1/profiles/1/photo').body(), photo)
        options.locator('#cv_text').fill('')
        options.get_by_role('button',name='Save profile',exact=True).click()
        expect(options.locator('#status')).to_contain_text('Remove saved base CVs')
        options.once('dialog',lambda dialog:dialog.accept())
        options.get_by_role('button',name='Remove saved base CVs',exact=True).click()
        expect(options.locator('#status')).to_contain_text('Base CVs removed')
        saved=self.context.request.get(self.origin+'/api/v1/profiles').json()['items'][0]
        self.assertIsNone(saved['cv'])
        self.assertEqual(saved['photo'], photo_metadata)
        self.assertEqual(self.context.request.get(self.origin+'/api/v1/profiles/1/photo').body(), photo)
