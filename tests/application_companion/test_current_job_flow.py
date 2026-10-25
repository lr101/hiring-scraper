"""Current employer job -> ChatGPT -> imported documents, entirely in extension UI."""
import json
import os
import re
from pathlib import Path
from playwright.sync_api import expect
from .test_production_preparation import ProductionHarness
from .test_application_journey import LISTING
from .test_tex_extension import tex_source


class CurrentJobFlowTests(ProductionHarness):
    def test_prompt_import_and_download_without_opening_radius(self):
        template = json.loads((Path(__file__).parents[2] / 'hiring_scraper/app/latex_template.json').read_text())
        body = tex_source().split('\\begin{document}\n', 1)[1].split('\\end{document}', 1)[0]
        source = template['preamble'] + body + template['ending']
        private_cv = os.environ.get('APPLICATION_TEST_CV_PATH')
        if private_cv: source = Path(private_cv).read_text()
        response = self.context.request.put(self.origin + '/api/v1/profiles/1/cv?filename=cv-en.tex&language=en', data=source.encode(), headers={'Content-Type':'application/octet-stream'})
        self.assertTrue(response.ok, response.text())
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#radius-origin').fill(self.origin)
        options.locator('#radius-connect').click()
        expect(options.locator('#radius-status')).to_contain_text('1 jobs cached')
        options.close()
        self.worker.evaluate("async ()=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.jobs.push({id:2000,title:'Unrelated role',company_name:'Research Labs',url:'https://employer.example/jobs/2000',active:true});await chrome.storage.local.set({radiusVault:v});}")
        self.context.route('https://chatgpt.com/**', lambda route: route.fulfill(body='<title>ChatGPT test</title><h1>ChatGPT</h1>'))
        self.page.goto(LISTING)
        popup = self.popup()
        expect(popup.locator('#job-entries .job-entry')).to_have_count(1)
        expect(popup.locator('#job-entries')).not_to_contain_text('Unrelated role')
        # Mock only the external navigation boundary; the real popup builds/copies
        # its prompt and Chrome still creates the tab. No live ChatGPT calls.
        popup.evaluate('''() => { const create=chrome.tabs.create.bind(chrome.tabs); chrome.tabs.create=details=>{window.chatgptTarget=details.url;return create({...details,url:'about:blank#chatgpt-test'});}; }''')
        with self.context.expect_page() as created:
            popup.get_by_role('button', name='Prepare with ChatGPT', exact=True).click()
        chat = created.value
        chat.wait_for_load_state()
        self.assertEqual(popup.evaluate('window.chatgptTarget'),'https://chatgpt.com/')
        popup.close()
        self.page.bring_to_front()
        popup = self.popup()
        expect(popup.locator('#prepared-prompt')).to_have_value(__import__('re').compile('.*SOURCE PAYLOAD', __import__('re').S))
        payload = json.loads(popup.locator('#prepared-prompt').input_value().split('SOURCE PAYLOAD (quoted data):')[1])
        self.assertEqual(payload['job']['id'],1574)
        if private_cv:
            self.assertEqual(len(payload['cv_history']['experiences']),4)
            self.assertEqual(len(payload['cv_history']['education']),2)
            self.assertIn('70%',payload['cv_history']['experiences'][0]['description'])
        else:
            self.assertEqual(payload['cv_history']['experiences'][0]['description'],'Built research tools.')
        urls = self.worker.evaluate('async () => (await chrome.tabs.query({})).map(tab=>tab.url)')
        self.assertFalse(any('/jobs/1574/apply' in url for url in urls))
        pack = dict(format='radius-application-v1', job_id=1574, profile_id=1, cv_updated_at=payload['cv_updated_at'], cv='Engineer\nBuilt research tools.', cover_letter='Dear team,\nI build research tools.', application_answers='None', review_notes='Reviewed', latex_documents={'cv':source})
        popup.get_by_text('Import ChatGPT result', exact=True).click()
        popup.locator('#result-input').fill(json.dumps({**pack,'job_id':2000}))
        popup.get_by_role('button', name='Use returned result', exact=True).click()
        expect(popup.locator('#status')).to_contain_text('current job')
        unsafe = source.replace('\\begin{document}\n', '\\begin{document}\n'+r'\directlua{os.execute("id")}'+'\n',1)
        popup.locator('#result-input').fill(json.dumps({**pack,'latex_documents':{'cv':unsafe}}))
        popup.get_by_role('button', name='Use returned result', exact=True).click()
        # Source-preservation validation can reject before the renderer grammar.
        # Assert rejection and the unchanged cache rather than a layer's wording.
        expect(popup.locator('#status')).to_have_class(re.compile(r'\berror\b'))
        cache = self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault.packages")
        self.assertNotIn('1574:1',cache)
        popup.locator('#result-input').fill(json.dumps(pack))
        popup.get_by_role('button', name='Use returned result', exact=True).click()
        expect(popup.locator('#status')).to_contain_text('connected')
        with popup.expect_download() as downloaded:
            popup.get_by_role('button', name='Download CV source', exact=True).click()
        self.assertEqual(Path(downloaded.value.path()).read_text(),source)
        with popup.expect_download(timeout=25000) as pdf_download:
            popup.get_by_role('button',name='Download CV PDF',exact=True).click()
        self.assertTrue(Path(pdf_download.value.path()).read_bytes().startswith(b'%PDF-'))
        chat.close()

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
        self.assertEqual(saved['application_details']['experiences'][0]['id'],'legacy-1')
        options.locator('#cv_text').fill('')
        options.get_by_role('button',name='Save profile',exact=True).click()
        expect(options.locator('#status')).to_contain_text('Remove saved base CVs')
        options.once('dialog',lambda dialog:dialog.accept())
        options.get_by_role('button',name='Remove saved base CVs',exact=True).click()
        expect(options.locator('#status')).to_contain_text('Base CVs removed')
        saved=self.context.request.get(self.origin+'/api/v1/profiles').json()['items'][0]
        self.assertIsNone(saved['cv'])
