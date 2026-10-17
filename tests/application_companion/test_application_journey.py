import json,time
from playwright.sync_api import expect
from .test_extension import ExtensionHarness

ORIGIN='https://employer.example'
LISTING=ORIGIN+'/jobs/1574'
ATS='https://ats.example/application?jobId=1574'
OTHER=ORIGIN+'/jobs/2000'
FORM='''<title>Employer application</title><form><label>First name<input id=first></label><label>Email<input id=email type=email></label><label>Cover letter<textarea id=letter></textarea></label><label>Why this role?<textarea id=why></textarea></label><label>CV<input type=file id=cv></label><button type=submit>Submit</button></form><script>window.submits=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();submits++}</script>'''
class JourneyBrowserTests(ExtensionHarness):
    def route(self,route):
        url=route.request.url
        if url in [LISTING,OTHER]:route.fulfill(content_type='text/html',body=f'<title>Research project manager</title><h1>Research project manager</h1><a href="{ATS}" target=_blank rel=noopener>Apply now</a>')
        elif url.startswith('https://radius.example/'):route.fulfill(content_type='text/html',body='<title>Radius preparation</title>')
        elif url.startswith('https://ats.example/'):route.fulfill(content_type='text/html',body=FORM)
        else:super().route(route)
    def seed(self,prepared=False):
        self.save_example()
        options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.evaluate('''async ({applicant,prepared,listing,other})=>{
          const {profileStamp}=await import('./vault.js');const stamp=await profileStamp(applicant);
          const pack={format:'radius-application-v1',job_id:1574,profile_id:1,cv_updated_at:'cv1',cv:[{id:12,text:'Prepared CV bullet'}],cover_letter:'Prepared letter for this role.',application_answers:'Q: Why this role?\\nA: I coordinate research projects.'};
          await chrome.storage.local.set({radiusVault:{settings:{origin:'https://radius.example',api_origin:'https://radius.example',profile_id:1,profile_name:'Research profile',cv_updated_at:'cv1'},profiles:[],jobs:[{id:1574,title:'Research project manager',company_name:'Research Labs',url:listing,active:true},{id:2000,title:'Engineering coordinator',company_name:'Research Labs',url:other,active:true}],bindings:[],packages:prepared?{'1574:1':{job_id:1574,profile_id:1,cv_updated_at:'cv1',profile_stamp:stamp,saved_at:Date.now(),package:pack}}:{},synced_at:Date.now()}});
        }''',{'applicant':self.applicant,'prepared':prepared,'listing':LISTING,'other':OTHER});options.close()
    def badge(self,url,want):
        end=time.monotonic()+5
        while time.monotonic()<end:
            text=self.worker.evaluate('''async url=>{const tab=(await chrome.tabs.query({})).find(t=>t.url===url);return tab?await chrome.action.getBadgeText({tabId:tab.id}):''}''',url)
            if text==want:return
            self.page.wait_for_timeout(40)
        self.assertEqual(text,want)
    def test_discovered_job_is_grey_and_opens_its_radius_preparation(self):
        self.seed();self.page.goto(LISTING);self.badge(LISTING,'•');popup=self.popup()
        expect(popup.locator('#job-entries')).to_contain_text('Research project manager')
        expect(popup.locator('#job-entries .job-entry')).to_have_class(__import__('re').compile('.*discovered.*'))
        popup.get_by_role('button',name='Prepare with ChatGPT',exact=True).click()
        end=time.monotonic()+5
        while time.monotonic()<end:
            urls=self.worker.evaluate('async ()=>(await chrome.tabs.query({})).map(t=>t.url)')
            if 'https://radius.example/jobs/1574/apply?profile_id=1' in urls:break
            self.page.wait_for_timeout(40)
        self.assertIn('https://radius.example/jobs/1574/apply?profile_id=1',urls)
    def test_prepared_badge_is_exact_and_apply_new_tab_keeps_the_job_package(self):
        self.seed(True);self.page.goto(LISTING);self.badge(LISTING,'1');popup=self.popup()
        expect(popup.locator('#job-entries')).to_contain_text('CV prepared')
        with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now',exact=True).click()
        application=event.value;application.wait_for_load_state();self.badge(ATS,'1')
        popup.close();self.page=application;popup=self.popup();self.fill(popup)
        self.assertEqual(self.page.locator('#letter').input_value(),'Prepared letter for this role.')
        self.assertEqual(self.page.locator('#why').input_value(),'I coordinate research projects.')
        self.assertEqual(self.page.evaluate('window.submits'),0)
        popup.close();self.page.goto(OTHER);self.badge(OTHER,'•')
    def test_apply_redirect_and_later_explicit_job_step_keep_the_binding(self):
        from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
        from threading import Thread
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/start':self.send_response(302);self.send_header('Location',target);self.end_headers()
                else:self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(FORM.encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        start=f'http://127.0.0.1:{server.server_port}/start';target=f'http://localhost:{server.server_port}/application?jobId=1574'
        self.context.route(f'http://127.0.0.1:{server.server_port}/**',lambda r:r.continue_())
        self.context.route(f'http://localhost:{server.server_port}/**',lambda r:r.continue_())
        self.seed(True);self.page.goto(LISTING);popup=self.popup()
        self.page.locator('a').evaluate("(el,url)=>{el.href=url;el.target='_self';}",start)
        self.page.get_by_role('link',name='Apply now').click();self.page.wait_for_url(target);self.badge(target,'1')
        popup.close();popup=self.popup();self.fill(popup)
        self.page.locator('form').evaluate("(el,url)=>{const a=document.createElement('a');a.href=url;a.textContent='Next step';el.after(a);}",target+'&step=2')
        self.page.get_by_role('link',name='Next step').click();self.page.wait_for_url(target+'&step=2');self.badge(target+'&step=2','1')
        popup.close();popup=self.popup();self.fill(popup);self.assertEqual(self.page.locator('#letter').input_value(),'Prepared letter for this role.')
    def test_unknown_application_can_be_linked_to_a_recent_prepared_job(self):
        self.seed(True);self.page.goto(LISTING);popup=self.popup();popup.get_by_role('button',name='View documents',exact=True).click()
        self.context.route('https://other-application.example/role/7',lambda r:r.fulfill(content_type='text/html',body=FORM))
        popup.close();self.page.goto('https://other-application.example/role/7');popup=self.popup()
        popup.locator('#continue-entry').get_by_role('button',name='Use on this page',exact=True).click()
        expect(self.page.locator('#letter')).to_have_value('Prepared letter for this role.')
        self.badge('https://other-application.example/role/7','1')
    def test_shared_url_requires_explicit_job_selection(self):
        self.seed(True)
        self.worker.evaluate('''async url=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');const first=v.packages['1574:1'];v.packages['2000:1']={...first,job_id:2000,package:{...first.package,job_id:2000,cover_letter:'Letter for the second role.'}};v.bindings=[{job_id:1574,url,kind:'confirmed'},{job_id:2000,url,kind:'confirmed'}];await chrome.storage.local.set({radiusVault:v});}''',ATS)
        self.page.goto(ATS);self.badge(ATS,'2');popup=self.popup();expect(popup.locator('#fill')).to_be_disabled()
        popup.locator('[data-job-id="2000"]').get_by_role('button',name='Fill this application',exact=True).click()
        expect(self.page.locator('#letter')).to_have_value('Letter for the second role.')
    def test_generic_same_url_form_requires_new_confirmation_after_fill(self):
        self.seed(True);self.page.goto(LISTING);popup=self.popup();popup.get_by_role('button',name='View documents',exact=True).click();popup.close()
        self.page.goto('https://ats.example/apply');popup=self.popup()
        popup.locator('#continue-entry').get_by_role('button',name='Use on this page',exact=True).click()
        expect(self.page.locator('#letter')).to_have_value('Prepared letter for this role.')
        self.page.evaluate("document.title='Different requisition';document.querySelector('#letter').value='';document.querySelector('#why').value='';")
        popup.locator('#fill').click();expect(popup.locator('#status')).not_to_have_text('Filling known details…')
        self.assertEqual(self.page.locator('#letter').input_value(),'')
        self.assertEqual(self.page.locator('#why').input_value(),'')
    def test_generic_form_swap_between_worker_validation_and_injected_fill_is_rejected(self):
        self.seed(True);self.page.goto(LISTING);popup=self.popup();popup.get_by_role('button',name='View documents',exact=True).click();popup.close()
        self.page.goto('https://ats.example/apply');popup=self.popup()
        # Intercept only at the real Chrome boundary; all page mutation/filling still runs in Chromium.
        self.worker.evaluate('''() => {
          const execute = chrome.scripting.executeScript.bind(chrome.scripting);
          chrome.scripting.executeScript = async details => {
            if (details.func?.toString().includes('applicationCompanion.run')) {
              await execute({target: details.target, func: () => { document.title = 'Different requisition'; }});
            }
            return execute(details);
          };
        }''')
        popup.locator('#continue-entry').get_by_role('button',name='Use on this page',exact=True).click()
        expect(popup.locator('#result-summary')).to_be_visible()
        self.assertEqual(self.page.locator('#letter').input_value(),'')
        self.assertEqual(self.page.locator('#why').input_value(),'')
        self.assertEqual(self.page.locator('#first').input_value(),'')
