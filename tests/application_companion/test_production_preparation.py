"""Exercise the shipped extension with the actual production API and React build."""
import json
import hashlib
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen
from zipfile import ZipFile

from playwright.sync_api import expect
from .test_extension import ExtensionHarness, ROOT
from .test_application_journey import ATS, LISTING, FORM
from tests import pg


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
        env = dict(os.environ, DATABASE_URL=pg.server_url(), HIRING_FRONTEND_DIR=str(project / 'frontend/dist'), PYTHONPATH=str(project))
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
        self.cv_source = tex_source()
        if changed:
            self.cv_source = self.cv_source.replace('Built research tools.', 'Built new research tools.')
        uploaded = self.context.request.put(self.origin + '/api/v1/profiles/1/cv?filename=base.tex&language=en',
            data=self.cv_source.encode(), headers={'Content-Type': 'application/x-tex'})
        self.assertTrue(uploaded.ok, uploaded.text())
        return self.context.request.get(self.origin + '/api/v1/profiles').json()['items'][0]['cv']['updated_at']

    def setup_preparation(self):
        version = self.upload_base()
        self.page.goto(self.origin + '/jobs/1574/apply?profile_id=1')
        expect(self.page.get_by_role('heading', name='Prepare your application')).to_be_visible()
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#radius-origin').fill(self.origin)
        options.locator('#radius-connect').click()
        expect(options.locator('#radius-status')).to_contain_text('1 jobs cached')
        options.close()
        return version

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

    def test_uncached_posting_is_recognized_and_server_search_can_link_another_job(self):
        self.setup_preparation()
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
        with self.context.expect_page() as opened:
            popup.locator('#job-search-results').get_by_role('button',name='Prepare documents',exact=True).click()
        application = opened.value
        application.wait_for_load_state('domcontentloaded')
        self.assertTrue(application.url.startswith(self.origin + '/jobs/'))
        self.assertTrue(application.url.endswith('/apply?profile_id=1'))
        expect(application.get_by_role('heading',name='Prepare your application')).to_be_visible()
        bindings=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.bindings")
        self.assertTrue(any(binding['url']=='https://employer.example/unlinked' for binding in bindings))

    def test_delayed_search_response_cannot_replace_the_current_query(self):
        self.setup_preparation()
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
        self.setup_preparation()
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
        self.setup_preparation()
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
        self.setup_preparation()
        self.worker.evaluate("""async()=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.jobs=Array.from({length:2000},(_,i)=>({id:10000+i,title:'Cached role',company_name:'Labs',url:`https://example.org/jobs/${10000+i}`,active:true}));await chrome.storage.local.set({radiusVault:v});}""")
        options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
        saved=options.evaluate("()=>chrome.runtime.sendMessage({type:'add-job',job:{profile_id:1,url:'https://employer.example/jobs/3001',title:'New specialist'}})")
        self.assertTrue(saved['ok'])
        jobs=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.jobs")
        self.assertLessEqual(len(jobs),2000)
        self.assertTrue(any(job['id']==saved['result']['job']['id'] for job in jobs))

    def test_captured_form_arriving_during_mount_is_read(self):
        self.setup_preparation()
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
        expect(prepared.get_by_text('1 questions captured by the application companion.', exact=False)).to_be_visible()


    def test_companion_link_opens_the_v2_editor_and_downloads_a_verified_pdf(self):
        from pypdf import PdfReader
        from tests.test_application_documents_v2_unit import letter

        self.setup_preparation()
        endpoint = self.origin + '/api/v1/profiles/1/application-documents/letter?job_id=1574'
        saved = self.context.request.put(endpoint, data={'body': letter(), 'expected_version': 0})
        self.assertTrue(saved.ok, saved.text())

        self.page.goto(LISTING)
        popup = self.popup()
        with self.context.expect_page() as opened:
            popup.get_by_role('button', name='Prepare documents', exact=True).click()
        application = opened.value
        expect(application.get_by_role('heading', name='Prepare your application')).to_be_visible()
        with (
            application.expect_download(timeout=30000) as downloaded,
            application.expect_response(
                lambda response: '/letter/pdf?' in response.url and response.status != 202,
                timeout=30000,
            ) as rendered,
        ):
            with application.expect_response(lambda response: '/letter/pdf?' in response.url) as first:
                application.get_by_role('button', name='Download PDF', exact=True).click()
            self.assertEqual(first.value.status, 202)
            # Claim the real queue row and run the shipped renderer without
            # starting the long-lived inference worker or touching a subscription.
            subprocess.run([sys.executable, '-c', (
                'from hiring_scraper.app.application_preparation_queue import process_render_once; '
                'from hiring_scraper.app.application_preparation_worker import render; '
                'assert process_render_once(render)'
            )], cwd=ROOT.parents[1], check=True, timeout=25, env={**os.environ, 'DATABASE_URL': pg.url()})

            response = rendered.value
            self.assertEqual(response.status, 200, response.text() if not response.ok else '')

        artifact = downloaded.value
        self.assertEqual(artifact.suggested_filename, 'application-letter-v1.pdf')
        reader = PdfReader(artifact.path())
        self.assertEqual(len(reader.pages), 1)
        self.assertIn('I build reliable services.', reader.pages[0].extract_text())

    def test_document_editor_blocks_rich_drops_and_removes_rich_nodes(self):
        from tests.test_application_documents_v2_unit import letter

        self.upload_base()
        endpoint = self.origin + '/api/v1/profiles/1/application-documents/letter?job_id=1574'
        saved = self.context.request.put(endpoint, data={'body': letter(), 'expected_version': 0})
        self.assertTrue(saved.ok, saved.text())
        # Force the same editor path used by browsers without plaintext-only.
        self.page.add_init_script("""(() => {
          const property = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'contentEditable');
          Object.defineProperty(HTMLElement.prototype, 'contentEditable', {
            ...property,
            set(value) { property.set.call(this, value === 'plaintext-only' ? 'true' : value); }
          });
        })();""")
        self.page.goto(self.origin + '/jobs/1574/apply?profile_id=1')
        paragraph = self.page.locator('[data-editable-unit="u-p1"]')
        expect(paragraph).to_have_attribute('contenteditable', 'true')
        result = paragraph.evaluate("""el => {
          const drop = new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: new DataTransfer()});
          drop.dataTransfer.setData('text/html', '<b>Fictional drop</b>');
          el.dispatchEvent(drop);
          el.append(document.createElement('b'));
          el.dispatchEvent(new InputEvent('input', {bubbles: true}));
          return [drop.defaultPrevented, el.childElementCount, el.textContent];
        }""")
        self.assertEqual(result, [True, 0, 'I build reliable services.'])

    def test_structured_document_conflict_preserves_the_local_edit(self):
        from tests.test_application_documents_v2_unit import letter

        self.upload_base()
        api = self.origin + '/api/v1/profiles/1/application-documents'
        endpoint = f'{api}/letter?job_id=1574'
        initial = self.context.request.put(endpoint, data={'body': letter(), 'expected_version': 0})
        self.assertTrue(initial.ok, initial.text())
        self.page.goto(self.origin + '/jobs/1574/apply?profile_id=1')
        paragraph = self.page.locator('[data-editable-unit="u-p1"]')
        expect(paragraph).to_have_text('I build reliable services.')
        server_edit = self.context.request.put(endpoint, data={
            'body': letter(paragraph='A newer saved paragraph.'), 'expected_version': 1,
            'expected_revision': initial.json()['revision'],
        })
        self.assertTrue(server_edit.ok, server_edit.text())
        paragraph.fill('My reviewed paragraph.')
        expect(self.page.get_by_text('Your local edits are still here.', exact=False)).to_be_visible()
        expect(paragraph).to_have_text('My reviewed paragraph.')
        overwrite = self.page.get_by_role('button', name='Overwrite with my edits')
        expect(overwrite).to_be_enabled()
        overwrite.click()
        expect(self.page.get_by_text('Version 3 · English', exact=True)).to_be_visible()
        body = self.context.request.get(f'{api}?job_id=1574').json()['letter']['body']
        self.assertEqual(body['paragraphs'][0]['text'], 'My reviewed paragraph.')
