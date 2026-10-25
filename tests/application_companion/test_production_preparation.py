"""Exercise the shipped extension with the actual production API and React build."""
import json
import hashlib
import io
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen
from zipfile import ZipFile

from playwright.sync_api import expect
from .test_extension import ExtensionHarness, EXTENSION, ROOT
from .test_application_journey import ATS, LISTING, FORM


class ProductionHarness(ExtensionHarness):
    # Playwright's service_workers=block init script reads navigator.serviceWorker
    # in every frame and throws in the app's deliberately sandboxed previews.
    # This harness serves Radius locally and intercepts all employer requests.
    service_workers = "allow"
    def setUp(self):
        self.server_directory = TemporaryDirectory()
        self.addCleanup(self.server_directory.cleanup)
        project = ROOT.parents[1]
        if not (project / 'frontend/dist/index.html').is_file():
            self.fail('Build the production frontend before running extension browser tests')
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        self.origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
        env = dict(os.environ, DATABASE_URL=f'sqlite:///{self.server_directory.name}/test.sqlite3', HIRING_FRONTEND_DIR=str(project / 'frontend/dist'), PYTHONPATH=str(project))
        self.server_log = open(Path(self.server_directory.name) / 'server.log', 'w+')
        self.addCleanup(self.server_log.close)
        self.server = subprocess.Popen([sys.executable, str(ROOT / 'production_server.py'), '--fd', str(listener.fileno())], pass_fds=[listener.fileno()], env=env, stdout=self.server_log, stderr=subprocess.STDOUT)
        listener.close()
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urlopen(self.origin + '/health/ready', timeout=1):
                    break
            except OSError:
                if self.server.poll() is not None:
                    self.server_log.seek(0)
                    self.fail(self.server_log.read())
                time.sleep(.05)
        else:
            self.fail('Isolated production API did not become ready')
        # Only loopback test origin is pregranted. Runtime is otherwise unchanged.
        self.extension_path = Path(self.server_directory.name) / 'extension'
        with urlopen(self.origin + '/downloads/radius-application-companion.zip') as response:
            shipped = response.read()
        with urlopen(self.origin + '/downloads/radius-application-companion.zip.sha256') as response:
            checksum = response.read().decode()
        self.assertEqual(checksum, hashlib.sha256(shipped).hexdigest() + '  radius-application-companion.zip\n')
        rebuilt_dir = Path(self.server_directory.name) / 'rebuilt'
        subprocess.run([sys.executable, str(project / 'scripts/build_extension.py'), '--output', str(rebuilt_dir)], check=True, stdout=subprocess.DEVNULL)
        self.assertEqual(shipped, (rebuilt_dir / 'radius-application-companion.zip').read_bytes(), 'Production download must match the tested source and CI package')
        with ZipFile(io.BytesIO(shipped)) as archive:
            archive.extractall(self.server_directory.name)
        manifest_path = self.extension_path / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['host_permissions'] = ['http://127.0.0.1/*']
        manifest_path.write_text(json.dumps(manifest))
        super().setUp()
        self.save_example()

    def stop_server(self):
        self.server.terminate()
        try:
            self.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait(timeout=5)

    def route(self, route):
        url = route.request.url
        if url.startswith(self.origin):
            route.continue_()
        elif url == LISTING:
            route.fulfill(content_type='text/html', body=f'<h1>Research project manager</h1><a href="{ATS}" target=_blank>Apply now</a>')
        elif url.startswith('https://ats.example/'):
            route.fulfill(content_type='text/html', body=FORM)
        else:
            super().route(route)



class ProductionPreparationTests(ProductionHarness):
    def upload_base(self, changed=False):
        from .test_tex_extension import tex_source
        template = json.loads((ROOT.parents[1] / 'hiring_scraper/app/latex_template.json').read_text())
        body = tex_source().split('\\begin{document}\n', 1)[1].split('\\end{document}', 1)[0]
        self.cv_source = template['preamble'] + body + template['ending']
        if changed:
            self.cv_source = self.cv_source.replace('Built research tools.', 'Built new research tools.')
        uploaded = self.context.request.put(self.origin + '/api/v1/profiles/1/cv?filename=base.tex&language=en',
            data=self.cv_source.encode(), headers={'Content-Type': 'application/x-tex'})
        self.assertTrue(uploaded.ok, uploaded.text())
        return self.context.request.get(self.origin + '/api/v1/profiles').json()['items'][0]['cv']['updated_at']

    def setup_preparation(self, attached=False):
        version = self.upload_base()
        self.page.goto(self.origin + '/jobs/1574/apply?profile_id=1')
        expect(self.page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#radius-origin').fill(self.origin)
        options.locator('#radius-connect').click()
        expect(options.locator('#radius-status')).to_contain_text('1 jobs cached')
        options.close()
        return version

    def import_package(self, page, payload, link=True):
        if link:
            from urllib.parse import quote
            page.goto(self.origin + '/jobs/1574/apply?profile_id=1#application=' + quote(json.dumps(payload), safe=''))
        else:
            page.get_by_text('Options', exact=True).click()
            page.get_by_role('button', name='I already have a result', exact=True).click()
            page.get_by_role('textbox', name='Application package', exact=True).fill(json.dumps(payload))
            page.get_by_role('button', name='Open documents', exact=True).click()
        expect(page.get_by_role('heading', name='Your documents are ready.')).to_be_visible()

    def unlinked_page(self):
        url='https://employer.example/unlinked'
        self.context.route(url,lambda route:route.fulfill(content_type='text/html',body='<h1>Employer page</h1>'))
        self.page.goto(url)

    def test_app_profile_details_survive_edit_and_sync_without_local_entry(self):
        self.context.set_default_timeout(60000)
        # The production fixture has a legacy profile with no preference lists.
        self.page.goto(self.origin + '/profile')
        self.page.get_by_role('link', name='Edit profile', exact=True).click()
        self.page.get_by_role('navigation', name='Profile setup').get_by_role('button', name='Application details', exact=False).click()
        self.page.get_by_label('First name', exact=True).fill('Alex')
        self.page.get_by_label('Last name', exact=True).fill('Example')
        self.page.get_by_label('Expected salary', exact=True).fill('75000')
        self.page.get_by_label('Salary currency (e.g. EUR)', exact=True).fill('EUR')
        self.page.get_by_label('Salary period (e.g. year)', exact=True).fill('year')
        self.page.get_by_role('button', name='Save profile', exact=True).click()
        expect(self.page.get_by_role('status')).to_contain_text('Profile saved', timeout=60000)
        expect(self.page.get_by_label('First name', exact=True)).to_have_value('Alex', timeout=60000)
        self.page.reload()
        expect(self.page.get_by_role('navigation', name='Profile setup').get_by_role('button', name='Application details', exact=False)).to_have_attribute('aria-current', 'step')
        expect(self.page.get_by_label('First name', exact=True)).to_have_value('Alex', timeout=60000)
        self.upload_base()
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#radius-origin').fill(self.origin)
        options.locator('#radius-connect').click()
        expect(options.locator('#radius-status')).to_contain_text('1 jobs cached', timeout=60000)
        options.close()
        applicant = self.worker.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(applicant['first_name'], 'Alex')
        self.assertEqual(applicant['preferences']['salary_amount'], '75000')
        self.assertIn('Built research tools', applicant['cv_text'])
        self.context.route('https://news.employer.example/**', lambda route: route.fulfill(content_type='text/html', body='<title>Company news</title>'))
        self.page.goto('https://news.employer.example/')
        popup = self.popup()
        expect(popup.locator('#site-status')).to_be_hidden()
        expect(popup.locator('#job-entries .job-entry')).to_have_count(0)

    def test_profile_history_is_derived_from_cv_without_manual_editor(self):
        self.setup_preparation()
        self.page.goto(self.origin + '/profile/1/edit')
        expect(self.page.get_by_role('button',name='Add work experience',exact=True)).to_have_count(0)
        applicant=self.worker.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(len(applicant['experiences']),1)
        self.assertEqual(applicant['experiences'][0]['company'],'Labs')
        self.assertEqual(applicant['experiences'][0]['description'],'Built research tools.')
        self.assertEqual(applicant['education'][0]['id'],'study-1')

    def test_question_capture_arriving_during_subscription_is_not_lost(self):
        self.upload_base()
        self.page.add_init_script('''(() => {
          const original=document.addEventListener.bind(document);
          document.addEventListener=(type,listener,options)=>{
            if(type==='radius-form-updated'&&!window.captureRaceTriggered){
              window.captureRaceTriggered=true;
              localStorage.setItem('radius:application-form:v1:1574:1',JSON.stringify({
                format:'radius-form-v1',job_id:1574,profile_id:1,form_id:'1'.repeat(64),
                url:'https://ats.example/jobs/1574',captured_at:Date.now(),
                fields:[{id:'q0',label:'What is your current city?',type:'text',required:true,
                  manual:false,accept:'',max_length:100,options:[]}]
              }));
              document.dispatchEvent(new Event('radius-form-updated'));
            }
            return original(type,listener,options);
          };
        })()''')
        self.page.goto(self.origin+'/jobs/1574/apply?profile_id=1')
        expect(self.page.get_by_text('This application step’s questions and answer limits are included in your brief.',exact=True)).to_be_visible()
        self.assertTrue(self.page.evaluate('window.captureRaceTriggered'))

    def test_production_cv_return_badge_apply_alias_and_fill(self):
        self.check_prepared_package_fill()

    def test_saved_profile_cv_return_sync_and_autofill(self):
        self.check_prepared_package_fill(attached=True)

    def test_uncached_posting_is_recognized_and_server_search_can_link_another_job(self):
        self.setup_preparation(attached=True)
        posting='https://employer.example/jobs/3001'
        self.context.route(posting+'*',lambda route:route.fulfill(content_type='text/html',body='<title>Fresh research role</title><h1>Fresh research role</h1>'))
        for url,title in [(posting,'Fresh research role'),('https://employer.example/jobs/3002','Uncached specialist')]:
            result=self.context.request.post(self.origin+'/api/v1/companion/jobs',data={'profile_id':1,'url':url,'title':title})
            self.assertTrue(result.ok,result.text())
        self.page.goto(posting+'?utm_source=mail');popup=self.popup()
        expect(popup.locator('#job-entries')).to_contain_text('Fresh research role')
        popup.close();self.unlinked_page();popup=self.popup()
        popup.get_by_text('Link this page to a job',exact=True).click()
        popup.locator('#job-query').fill('Uncached specialist')
        expect(popup.locator('#job-search-results')).to_contain_text('Uncached specialist')
        popup.evaluate("() => {chrome.tabs.create=async()=>({id:999});}")
        popup.locator('#job-search-results').get_by_role('button',name='Prepare with ChatGPT',exact=True).click()
        expect(popup.locator('#prepared-prompt')).not_to_have_value('')
        bindings=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.bindings")
        self.assertTrue(any(binding['url']=='https://employer.example/unlinked' for binding in bindings))

    def test_delayed_search_response_cannot_replace_the_current_query(self):
        self.setup_preparation(attached=True)
        for number,title in [(3001,'Earlier specialist'),(3002,'Latest specialist')]:
            result=self.context.request.post(self.origin+'/api/v1/companion/jobs',data={'profile_id':1,'url':f'https://employer.example/jobs/{number}','title':title})
            self.assertTrue(result.ok,result.text())
        self.worker.evaluate('''()=>{
          const original=globalThis.fetch;
          globalThis.fetch=async(input,options)=>{
            if(String(input).endsWith('/jobs/search?q=Earlier')) {
              globalThis.searchStarted=true;
              await new Promise(resolve=>{globalThis.releaseSearch=resolve;});
            }
            const response=await original(input,options);
            if(String(input).endsWith('/jobs/search?q=Earlier')) globalThis.searchFinished=true;
            return response;
          };
        }''')
        self.page.goto(LISTING);popup=self.popup()
        popup.close();self.unlinked_page();popup=self.popup()
        popup.get_by_text('Link this page to a job',exact=True).click()
        popup.locator('#job-query').fill('Earlier')
        deadline=time.monotonic()+5
        while not self.worker.evaluate('!!globalThis.searchStarted') and time.monotonic()<deadline:self.page.wait_for_timeout(20)
        self.assertTrue(self.worker.evaluate('!!globalThis.searchStarted'))
        popup.locator('#job-query').fill('Latest')
        expect(popup.locator('#job-search-results')).to_contain_text('Latest specialist')
        self.worker.evaluate('globalThis.releaseSearch()')
        deadline=time.monotonic()+5
        while not self.worker.evaluate('!!globalThis.searchFinished') and time.monotonic()<deadline:self.page.wait_for_timeout(20)
        self.assertTrue(self.worker.evaluate('!!globalThis.searchFinished'))
        popup.wait_for_timeout(300)
        expect(popup.locator('#job-search-results')).to_contain_text('Latest specialist')
        expect(popup.locator('#job-search-results')).not_to_contain_text('Earlier specialist')

    def test_badges_never_send_browsing_urls_and_lookup_skips_session_pages(self):
        self.setup_preparation(attached=True)
        self.context.route('https://unseen.example/**',lambda route:route.fulfill(content_type='text/html',body='<title>Uncached employer</title>'))
        self.worker.evaluate('''()=>{
          const original=globalThis.fetch;globalThis.lookupRequests=[];
          globalThis.fetch=(input,options)=>{
            if(String(input).includes('/jobs/lookup?')) lookupRequests.push(String(input));
            return original(input,options);
          };
        }''')
        self.page.goto('https://unseen.example/jobs/77?utm_source=mail')
        self.page.wait_for_timeout(200)
        self.assertEqual(self.worker.evaluate('lookupRequests'),[])
        popup=self.popup()
        requests=self.worker.evaluate('lookupRequests')
        self.assertGreaterEqual(len(requests),1)
        self.assertIn('url=https%3A%2F%2Funseen.example%2Fjobs%2F77',requests[0])
        self.assertNotIn('utm_source',requests[0])
        popup.close()
        for path in ['/login?token=private','/apply']:
            self.page.goto('https://unseen.example'+path);popup=self.popup();popup.close()
        self.assertTrue(all(request==requests[0] for request in self.worker.evaluate('lookupRequests')))

    def test_search_uses_server_results_even_when_ten_cached_jobs_match(self):
        self.setup_preparation(attached=True)
        jobs=[]
        for number in range(11):
            result=self.context.request.post(self.origin+'/api/v1/companion/jobs',data={'profile_id':1,'url':f'https://employer.example/jobs/{3000+number}','title':f'Specialist {number}'})
            self.assertTrue(result.ok,result.text());jobs.append(result.json()['job'])
        self.worker.evaluate("""async rows=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.jobs=rows.map(row=>({...row,active:row.is_active}));await chrome.storage.local.set({radiusVault:v});}""",jobs[:10])
        self.page.goto(LISTING);popup=self.popup()
        popup.close();self.unlinked_page();popup=self.popup()
        popup.get_by_text('Link this page to a job',exact=True).click()
        popup.locator('#job-query').fill('Specialist')
        expect(popup.locator('#job-search-results')).to_contain_text('Specialist 10')
        expect(popup.locator('#job-search-results .job-entry').first).to_contain_text('Specialist 10')

    def test_saving_an_uncached_job_keeps_the_catalog_bounded(self):
        self.setup_preparation(attached=True)
        self.worker.evaluate("""async()=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.jobs=Array.from({length:2000},(_,i)=>({id:10000+i,title:'Cached role',company_name:'Labs',url:`https://example.org/jobs/${10000+i}`,active:true}));await chrome.storage.local.set({radiusVault:v});}""")
        options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
        saved=options.evaluate("()=>chrome.runtime.sendMessage({type:'add-job',job:{profile_id:1,url:'https://employer.example/jobs/3001',title:'New specialist'}})")
        self.assertTrue(saved['ok'])
        jobs=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.jobs")
        self.assertLessEqual(len(jobs),2000)
        self.assertTrue(any(job['id']==saved['result']['job']['id'] for job in jobs))

    def check_prepared_package_fill(self, attached=False):
        version = self.setup_preparation(attached=attached)
        payload = {'format': 'radius-application-v1', 'job_id': 1574, 'profile_id': 1, 'cv_updated_at': version, 'cv': 'Alex Example. Coordinated research teams.', 'cover_letter': 'I coordinate research projects.', 'application_answers': 'Q: Why this role?\nA: I coordinate research teams.', 'review_notes': 'Review before applying.'}
        payload['latex_documents']={'cv':self.cv_source}
        self.import_package(self.page,payload,link=attached)
        saved = self.page.evaluate("JSON.parse(localStorage.getItem('radius:application-draft:v1:1574:1'))")
        self.assertIsInstance(saved['saved_at'], (int, float))
        source = self.context.new_page()
        source.goto(LISTING)
        self.page = source
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            packages = self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault.packages")
            if '1574:1' in packages:
                break
            self.page.wait_for_timeout(50)
        self.assertIn('1574:1', packages)
        popup = self.popup()
        expect(popup.locator('#job-entries')).to_contain_text('CV prepared')
        with self.context.expect_page() as event:
            self.page.get_by_role('link', name='Apply now').click()
        application = event.value
        application.wait_for_url(ATS)
        popup.close()
        self.page = application
        popup = self.popup()
        self.fill(popup)
        self.assertEqual(self.page.locator('#letter').input_value(), payload['cover_letter'])
        self.assertEqual(self.page.locator('#why').input_value(), 'I coordinate research teams.')
        self.assertEqual(self.page.evaluate('window.submits'), 0)
        with urlopen(self.origin + '/api/v1/profiles/1/applications') as response:
            self.assertEqual(json.load(response)['total'], 0, 'Preparing/filling must not change tracking stages')
        with urlopen(self.origin + '/downloads/radius-application-companion.zip') as response:
            self.assertEqual(response.read(2), b'PK')
    def test_return_link_import_and_changed_cv_prevent_stale_tailored_fill(self):
        from urllib.parse import quote
        version = self.setup_preparation()
        payload = {'format':'radius-application-v1','job_id':1574,'profile_id':1,'cv_updated_at':version,'cv':'Alex Example. Research coordination.','cover_letter':'Only for the original CV.','application_answers':'Q: Why this role?\nA: Original research experience.','review_notes':'Review.'}
        payload['latex_documents']={'cv':self.cv_source}
        self.import_package(self.page,payload)
        self.assertNotIn('#', self.page.url)
        expect(self.page.get_by_role('heading',name='Your documents are ready.')).to_be_visible()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            packages = self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault.packages")
            if '1574:1' in packages:break
            self.page.wait_for_timeout(50)
        self.assertIn('1574:1', packages)
        self.upload_base(changed=True)
        employer = self.context.new_page();employer.goto(LISTING);self.page = employer;popup = self.popup()
        with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now').click()
        application = event.value;application.wait_for_url(ATS);popup.close();self.page = application;popup = self.popup()
        popup.locator('#fill').click()
        expect(popup.locator('#status')).not_to_have_text('Filling known details…')
        self.assertEqual(self.page.locator('#letter').input_value(),'')
        self.assertEqual(self.page.locator('#why').input_value(),'')
        self.assertEqual(self.page.evaluate('window.submits'),0)

    def test_captured_questions_return_documents_and_select_files_through_real_api(self):
        self.check_captured_question_and_document_flow(attached=True)

    def test_captured_form_arriving_during_mount_is_read(self):
        self.setup_preparation(attached=True)
        # Deliver the bridge's storage write after the component's initial read
        # but before its event listener is registered.
        self.context.add_init_script("""(() => {
          const add = document.addEventListener.bind(document);
          let delivered = false;
          document.addEventListener = (name, listener, ...options) => {
            if (name === 'radius-form-updated' && !delivered) {
              delivered = true;
              localStorage.setItem('radius:application-form:v1:1574:1', JSON.stringify({
                format: 'radius-form-v1', job_id: 1574, profile_id: 1,
                form_id: 'a'.repeat(64), url: 'https://ats.example/apply',
                captured_at: Date.now(), fields: [{id: 'q0', label: 'Why this role?',
                  type: 'textarea', required: true, max_length: 200,
                  options: [], accept: '', manual: false}]
              }));
              document.dispatchEvent(new CustomEvent('radius-form-updated'));
            }
            return add(name, listener, ...options);
          };
        })()""")
        prepared = self.context.new_page()
        prepared.goto(self.origin + '/jobs/1574/apply?profile_id=1')
        expect(prepared.get_by_text(
            'This application step’s questions and answer limits are included in your brief.',
            exact=True)).to_be_visible()

    def test_manual_import_captured_questions_and_document_exports(self):
        self.check_captured_question_and_document_flow(attached=False)

    def check_captured_question_and_document_flow(self, attached):
        from urllib.parse import quote
        from .test_form_preparation import QUESTIONS
        from pypdf import PdfReader
        version=self.setup_preparation(attached=attached)
        self.context.route('https://ats.example/**',lambda route:route.fulfill(content_type='text/html',body=QUESTIONS))
        self.page.goto(LISTING);popup=self.popup()
        with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now').click()
        popup.close();self.page=event.value;self.page.wait_for_url(ATS)
        self.page.locator('#why').fill('')
        popup=self.popup()
        # Capture stays in the extension; inspect the canonical capture before
        # explicitly opening the Radius editor to exercise its return bridge.
        tab_id=self.worker.evaluate("async url=>(await chrome.tabs.query({})).find(tab=>tab.url===url).id",self.page.url)
        captured_result=popup.evaluate("async tabId=>chrome.runtime.sendMessage({type:'scan-form',tabId,jobId:1574,openRadius:false})",tab_id)
        self.assertTrue(captured_result['ok'],captured_result)
        captured=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.forms['1574:1']")
        prepared=self.context.new_page()
        prepared.goto(self.origin+'/jobs/1574/apply?profile_id=1')
        expect(prepared.get_by_text('This application step’s questions and answer limits are included in your brief.',exact=True)).to_be_visible()
        payload={'format':'radius-application-v1','job_id':1574,'profile_id':1,'cv_updated_at':version,'cv':'Alex Example. Coordinated research teams.','cover_letter':'I coordinate research projects.','application_answers':'I coordinate research teams.','review_notes':'Review before applying.','form_answers':{'form_id':captured['form_id'],'answers':[{'id':'q0','value':'I coordinate research teams.'},{'id':'q1','value':'research'}]}}
        payload['latex_documents']={'cv':self.cv_source}
        self.import_package(prepared,payload,link=attached)
        prepared.get_by_role('button',name='Application answers',exact=True).click()
        answers=prepared.get_by_role('region',name='Prepared form answers')
        expect(answers.locator('strong')).to_have_text(['Why this role?','Preferred team'])
        changed=json.loads(json.dumps(captured));changed['form_id']='0'*64
        changed['fields'][0]['label']='Explain your disciplinary history'
        changed['fields'][1]['label']='Choose a legal declaration'
        prepared.evaluate("form=>{localStorage.setItem('radius:application-form:v1:1574:1',JSON.stringify(form));document.dispatchEvent(new Event('radius-form-updated'))}",changed)
        expect(answers.locator('strong')).to_have_text(['q0','q1'])
        expect(answers).to_contain_text('earlier capture')
        expect(answers).to_contain_text('I coordinate research teams.')
        prepared.evaluate("form=>{localStorage.setItem('radius:application-form:v1:1574:1',JSON.stringify(form));document.dispatchEvent(new Event('radius-form-updated'))}",captured)
        expect(answers.locator('strong')).to_have_text(['Why this role?','Preferred team'])
        prepared.get_by_role('button',name='CV',exact=True).click()
        with prepared.expect_download() as download:prepared.get_by_role('button',name='Download LaTeX source',exact=True).click()
        self.assertEqual(Path(download.value.path()).read_text(),self.cv_source)
        prepared.get_by_role('button',name='Cover letter',exact=True).click()
        with prepared.expect_download(timeout=25000) as download:prepared.get_by_role('button',name='Download PDF',exact=True).click()
        self.assertIn('I coordinate research projects.', ''.join(page.extract_text() for page in PdfReader(download.value.path()).pages))
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            packages=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.packages")
            if packages.get('1574:1',{}).get('package',{}).get('form_answers'):break
            self.page.wait_for_timeout(50)
        self.assertEqual(packages['1574:1']['package']['form_answers'],payload['form_answers'])
        popup.close();popup=self.popup();self.fill(popup)
        expect(self.page.locator('#why')).to_have_value('I coordinate research teams.')
        expect(self.page.locator('#team')).to_have_value('research')
        self.page.evaluate("window.submits=0;document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();submits++})")
        popup.get_by_role('button',name='Attach prepared documents',exact=True).click()
        expect(popup.locator('#status')).to_contain_text('2 documents selected',timeout=30000)
        self.assertEqual(self.page.locator('#cv').evaluate('el=>el.files[0].type'),'application/pdf')
        self.assertEqual(self.page.locator('#letter-file').evaluate('el=>el.files[0].type'),'application/pdf')
        self.assertTrue(self.page.locator('#cv').evaluate("async el=>(await el.files[0].arrayBuffer()).byteLength>100"))
        expect(self.page.locator('#consent')).not_to_be_checked()
        self.assertEqual(self.page.evaluate('window.submits'),0)
        self.assertEqual(self.context.request.get(self.origin+'/api/v1/profiles/1/applications').json()['total'],0)
