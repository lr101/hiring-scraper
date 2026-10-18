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


class ProductionPreparationTests(ExtensionHarness):
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

    def setup_preparation(self, attached=False):
        if attached:
            uploaded = self.context.request.put(self.origin + '/api/v1/profiles/1/cv?filename=original.txt',
                data=b'Alex Example. Contact alex@example.invalid. Coordinated research teams.',
                headers={'Content-Type': 'application/octet-stream'})
            self.assertTrue(uploaded.ok, uploaded.text())
        self.page.goto(self.origin + '/jobs/1574/apply?profile_id=1')
        if attached:
            expect(self.page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
            version = self.context.request.get(self.origin + '/api/v1/profiles').json()['items'][0]['cv']['updated_at']
        else:
            self.page.get_by_role('textbox', name='Original CV text', exact=True).fill('Alex Example. Contact alex@example.invalid. Coordinated research teams.')
            self.page.get_by_role('button', name='Save CV', exact=True).click()
            expect(self.page.locator('.application-cv-ready')).to_be_visible()
            version = self.page.evaluate("JSON.parse(localStorage.getItem('radius:application-cv:v1:1')).updated_at")
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#radius-origin').fill(self.origin)
        options.locator('#radius-connect').click()
        expect(options.locator('#radius-status')).to_contain_text('1 discovered jobs synced')
        options.close()
        return version

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
        popup.get_by_text('Link a discovered job',exact=True).click()
        popup.locator('#job-query').fill('Uncached specialist')
        expect(popup.locator('#job-search-results')).to_contain_text('Uncached specialist')
        popup.locator('#job-search-results').get_by_role('button',name='Prepare with ChatGPT',exact=True).click()
        bindings=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.bindings")
        self.assertTrue(any(binding['url']==posting for binding in bindings))

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
        popup.get_by_text('Link a discovered job',exact=True).click()
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
        popup.get_by_text('Link a discovered job',exact=True).click()
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
        if attached:
            from urllib.parse import quote
            self.page.goto(self.origin + '/jobs/1574/apply?profile_id=1#application=' + quote(json.dumps(payload), safe=''))
            expect(self.page.get_by_role('heading', name='Your documents are ready.')).to_be_visible()
        else:
            self.page.get_by_role('textbox', name='Returned application package', exact=True).fill(json.dumps(payload))
            self.page.get_by_role('button', name='Validate and save package', exact=True).click()
            expect(self.page.get_by_role('status')).to_contain_text('Prepared package saved')
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
        self.page.goto(self.origin + '/jobs/1574/apply?profile_id=1#application=' + quote(json.dumps(payload), safe=''))
        expect(self.page.get_by_role('status')).to_contain_text('Returned package validated')
        self.assertNotIn('#', self.page.url)
        self.assertIn('Alex Example', self.page.locator('.application-package-output').inner_text())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            packages = self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault.packages")
            if '1574:1' in packages:break
            self.page.wait_for_timeout(50)
        self.assertIn('1574:1', packages)
        self.page.get_by_role('textbox', name='Original CV text', exact=True).fill('Alex Example. A changed original CV. alex@example.invalid')
        self.page.get_by_role('button', name='Save CV', exact=True).click()
        employer = self.context.new_page();employer.goto(LISTING);self.page = employer;popup = self.popup()
        with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now').click()
        application = event.value;application.wait_for_url(ATS);popup.close();self.page = application;popup = self.popup()
        popup.locator('#fill').click()
        expect(popup.locator('#status')).not_to_have_text('Filling known details…')
        self.assertEqual(self.page.locator('#letter').input_value(),'')
        self.assertEqual(self.page.locator('#why').input_value(),'')
        self.assertEqual(self.page.evaluate('window.submits'),0)
