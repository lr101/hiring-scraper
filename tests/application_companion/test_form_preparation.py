"""Exercise scanned questions and uploads without allowing an employer request."""
import json
from playwright.sync_api import expect
from . import test_application_journey as journey
from .test_application_journey import LISTING, ATS
from .test_extension import ExtensionHarness

QUESTIONS = '''<title>Application questions</title><form><h1>Your application</h1>
<label>Why this role?<textarea id=why maxlength=180 required>Private existing answer</textarea></label>
<label>Preferred team<select id=team><option value="">Choose</option><option value=research>Research</option><option value=ops>Operations</option></select></label>
<label>CV<input id=cv type=file accept=".pdf,.docx" required></label>
<label>Cover letter<input id=letter-file type=file accept="application/pdf"></label>
<label><input type=checkbox id=consent required>I agree to the privacy policy</label>
<button type=submit>Submit application</button></form>'''

class FormPreparationTests(ExtensionHarness):
    seed = journey.JourneyBrowserTests.seed
    def route(self,route):
        url=route.request.url
        if url in [LISTING,journey.OTHER]:route.fulfill(content_type='text/html',body=f'<h1>Research project manager</h1><a href="{ATS}" target=_blank>Apply now</a>')
        elif url.startswith('https://radius.example/'):route.fulfill(content_type='text/html',body='<title>Radius preparation</title>')
        elif url.startswith('https://ats.example/'):route.fulfill(content_type='text/html',body=QUESTIONS)
        else:super().route(route)
    def test_scan_captures_constraints_without_applicant_values_and_opens_radius(self):
        self.seed(True)
        self.page.goto(LISTING);popup=self.popup()
        with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now').click()
        popup.close();self.page=event.value;self.page.wait_for_url(ATS)
        self.page.set_content(QUESTIONS)
        popup=self.popup()
        expect(popup.get_by_role('button',name='Prepare from these questions',exact=True)).to_be_visible()
        popup.get_by_role('button',name='Prepare from these questions',exact=True).click()
        expect(popup.locator('#status')).to_contain_text('questions captured')
        saved=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.forms['1574:1']")
        self.assertNotIn('Private existing answer',json.dumps(saved))
        why=next(field for field in saved['fields'] if field['label']=='Why this role?')
        self.assertEqual(why['max_length'],180)
        self.assertTrue(why['required'])
        self.assertEqual(len(saved['form_id']),64)
        team=next(field for field in saved['fields'] if field['label']=='Preferred team')
        self.assertEqual(team['options'][1],{'value':'research','label':'Research'})
        self.assertTrue(next(field for field in saved['fields'] if 'privacy' in field['label'])['manual'])

    def test_structured_answers_fill_only_the_unchanged_captured_form(self):
        self.seed(True);self.page.goto(LISTING);popup=self.popup()
        with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now').click()
        popup.close();self.page=event.value;self.page.wait_for_url(ATS)
        self.page.locator('#why').fill('');popup=self.popup()
        popup.get_by_role('button',name='Prepare from these questions',exact=True).click()
        expect(popup.locator('#status')).to_contain_text('questions captured')
        self.worker.evaluate("""async()=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.packages['1574:1'].package.application_answers='';v.packages['1574:1'].package.form_answers={form_id:v.forms['1574:1'].form_id,answers:[{id:'q0',value:'A concise structured answer.'},{id:'q1',value:'research'}]};await chrome.storage.local.set({radiusVault:v});}""")
        self.fill(popup)
        expect(self.page.locator('#why')).to_have_value('A concise structured answer.')
        expect(self.page.locator('#team')).to_have_value('research')
        expect(self.page.locator('#consent')).not_to_be_checked()
        self.page.locator('#why').fill('');self.page.locator('#team').select_option('')
        self.page.locator('#team').evaluate("el=>el.add(new Option('Another team','other'))")
        self.fill(popup)
        expect(self.page.locator('#why')).to_have_value('')
        expect(self.page.locator('#team')).to_have_value('')


    def test_explicit_attachment_selects_files_once_without_consent_or_submit(self):
        self.seed(True)
        self.worker.evaluate("""()=>{globalThis.exports=[];globalThis.fetch=async(input,options)=>{if(String(input).includes('/application-document?')){exports.push(JSON.parse(options.body));return new Response('%PDF-1.7\\nSynthetic trial document',{headers:{'Content-Type':'application/pdf'}})}return new Response(JSON.stringify({items:[{id:1,cv:{updated_at:'cv1'}}]}),{headers:{'Content-Type':'application/json'}})}}""")
        self.page.goto(LISTING);popup=self.popup()
        with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now').click()
        popup.close();self.page=event.value;self.page.wait_for_url(ATS)
        self.page.evaluate("window.submits=0;document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();window.submits++})")
        popup=self.popup();popup.get_by_role('button',name='Attach prepared documents',exact=True).click()
        expect(popup.locator('#status')).to_contain_text('2 documents selected')
        self.assertEqual(self.page.locator('#cv').evaluate('el=>el.files[0].type'),'application/pdf')
        self.assertEqual(self.page.locator('#letter-file').evaluate('el=>el.files[0].type'),'application/pdf')
        self.assertEqual(self.worker.evaluate('exports.length'),2)
        expect(self.page.locator('#consent')).not_to_be_checked()
        self.assertEqual(self.page.evaluate('window.submits'),0)
        popup.get_by_role('button',name='Attach prepared documents',exact=True).click()
        expect(popup.locator('#status')).to_contain_text('0 documents selected')
        self.assertEqual(self.worker.evaluate('exports.length'),2)

    def test_attachment_refuses_a_form_changed_during_export(self):
        self.check_changed_export(lambda: self.page.locator('#cv').evaluate("el=>el.accept='.docx'"))

    def test_attachment_refuses_changed_requisition_heading_during_export(self):
        self.check_changed_export(lambda: self.page.locator('h1').evaluate("el=>el.textContent='Another requisition'"))

    def check_changed_export(self, change):
        self.seed(True)
        self.worker.evaluate("""()=>{globalThis.fetch=async(input,options)=>{if(String(input).includes('/application-document?')){if(!globalThis.exportStarted){globalThis.exportStarted=true;await new Promise(resolve=>{globalThis.releaseExport=resolve})}return new Response('%PDF-1.7\\nSynthetic trial document',{headers:{'Content-Type':'application/pdf'}})}return new Response(JSON.stringify({items:[{id:1,cv:{updated_at:'cv1'}}]}),{headers:{'Content-Type':'application/json'}})}}""")
        self.page.goto(ATS)
        self.worker.evaluate("async url=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.bindings=[{job_id:1574,url,kind:'confirmed'}];await chrome.storage.local.set({radiusVault:v});}",ATS)
        popup=self.popup();popup.get_by_role('button',name='Attach prepared documents',exact=True).click()
        import time
        deadline=time.monotonic()+5
        while not self.worker.evaluate('!!globalThis.exportStarted') and time.monotonic()<deadline:self.page.wait_for_timeout(20)
        self.assertTrue(self.worker.evaluate('!!globalThis.exportStarted'))
        change()
        self.worker.evaluate('globalThis.releaseExport()')
        expect(popup.locator('#status')).to_contain_text('changed')
        self.assertEqual(self.page.locator('#cv').evaluate('el=>el.files.length'),0)
        self.assertEqual(self.page.locator('#letter-file').evaluate('el=>el.files.length'),0)

    def test_generic_upload_labels_use_unambiguous_native_document_ids(self):
        self.seed(True);self.page.goto(ATS)
        self.page.set_content('''<form><label>Email<input type=email></label><label>Phone<input></label><label>Attach<input id=resume type=file></label><label>Attach<input id=cover_letter type=file></label><label>Attach<input id=question_123 type=file></label><label>Expected salary* (required)<input></label></form>''')
        self.worker.evaluate("async url=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.bindings=[{job_id:1574,url,kind:'confirmed'}];await chrome.storage.local.set({radiusVault:v});}",ATS)
        popup=self.popup();popup.get_by_role('button',name='Prepare from these questions',exact=True).click()
        expect(popup.locator('#status')).to_contain_text('questions captured')
        saved=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.forms['1574:1']")
        self.assertEqual([field.get('document_kind') for field in saved['fields'] if field['type']=='file'],['cv','cover_letter',''])
        self.assertTrue(saved['fields'][-1]['required'])

    def test_ambiguous_and_hidden_uploads_stay_manual_and_existing_file_is_preserved(self):
        self.seed(True);self.page.goto(ATS)
        self.page.set_content('''<form><label>Email<input type=email></label><label>Phone<input></label><label>CV<input id=cv-a type=file></label><label>CV<input id=cv-b type=file></label><label>Cover letter<input id=keep type=file></label><input id=hidden type=file hidden></form>''')
        self.page.locator('#keep').set_input_files({'name':'reviewed.pdf','mimeType':'application/pdf','buffer':b'Existing reviewed file'})
        self.worker.evaluate("async url=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.bindings=[{job_id:1574,url,kind:'confirmed'}];globalThis.fetch=async()=>new Response(JSON.stringify({items:[{id:1,cv:{updated_at:'cv1'}}]}));await chrome.storage.local.set({radiusVault:v});}",ATS)
        popup=self.popup();popup.get_by_role('button',name='Attach prepared documents',exact=True).click()
        expect(popup.locator('#status')).to_contain_text('0 documents selected')
        expect(popup.locator('#status')).to_contain_text('4 upload fields need manual review')
        self.assertEqual(self.page.locator('#keep').evaluate('el=>el.files[0].name'),'reviewed.pdf')
        self.assertEqual(self.page.locator('#cv-a').evaluate('el=>el.files.length'),0)
        self.assertEqual(self.page.locator('#cv-b').evaluate('el=>el.files.length'),0)

    def test_site_removing_selected_file_is_reported_as_unverified(self):
        self.seed(True);self.page.goto(ATS)
        self.page.locator('#cv').evaluate("el=>el.addEventListener('change',()=>{el.value=''})")
        self.worker.evaluate("async url=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.bindings=[{job_id:1574,url,kind:'confirmed'}];globalThis.fetch=async input=>String(input).includes('/application-document?')?new Response('%PDF-1.7\\nSynthetic trial document',{headers:{'Content-Type':'application/pdf'}}):new Response(JSON.stringify({items:[{id:1,cv:{updated_at:'cv1'}}]}));await chrome.storage.local.set({radiusVault:v});}",ATS)
        popup=self.popup();popup.get_by_role('button',name='Attach prepared documents',exact=True).click()
        expect(popup.locator('#status')).to_contain_text('did not retain')
        self.assertEqual(self.page.locator('#letter-file').evaluate('el=>el.files.length'),0)
        expect(self.page.locator('#consent')).not_to_be_checked()
