from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import json,shutil,time
from playwright.sync_api import expect
from .test_extension import ExtensionHarness,EXTENSION
from .test_application_journey import LISTING,OTHER,ATS,FORM
from .journey_demo import server

class RadiusConnectionTests(ExtensionHarness):
 def setUp(self):
  self.server=server(employer_origin='https://employer.example');Thread(target=self.server.serve_forever,daemon=True).start();self.origin=f'http://127.0.0.1:{self.server.server_port}'
  # Headless Chrome cannot click its native optional-permission prompt. This
  # test-only copy pregrants ONLY the loopback fixture origin. Runtime sources,
  # all other permissions and the production manifest remain unchanged.
  self.copy=TemporaryDirectory();self.extension_path=Path(self.copy.name)/'extension';shutil.copytree(EXTENSION,self.extension_path)
  path=self.extension_path/'manifest.json';manifest=json.loads(path.read_text());manifest['host_permissions']=['http://127.0.0.1/*'];path.write_text(json.dumps(manifest))
  super().setUp();self.save_example()
 def route(self,route):
  url=route.request.url
  if url.startswith(self.origin):route.continue_()
  elif url in [LISTING,OTHER]:route.fulfill(content_type='text/html',body=f'<h1>Research project manager</h1><a href="{ATS}" target=_blank rel=noopener>Apply now</a>')
  elif url.startswith('https://ats.example/'):route.fulfill(content_type='text/html',body=FORM)
  else:super().route(route)
 def tearDown(self):
  try:super().tearDown()
  finally:self.copy.cleanup();self.server.shutdown();self.server.server_close()
 def connect(self):
  options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html');options.locator('#radius-origin').fill(self.origin);options.locator('#radius-connect').click();expect(options.locator('#radius-status')).to_contain_text('2 jobs cached');options.close()
 def badge(self,url,want):
  end=time.monotonic()+8;text=''
  while time.monotonic()<end:
   text=self.worker.evaluate("async url=>{const tab=(await chrome.tabs.query({})).find(t=>t.url===url);return tab?await chrome.action.getBadgeText({tabId:tab.id}):''}",url)
   if text==want:return
   self.page.wait_for_timeout(40)
  self.assertEqual(text,want)
 def test_real_http_connection_and_automatic_return_to_apply_round_trip(self):
  self.connect();self.page.goto(LISTING);self.badge(LISTING,'•');popup=self.popup();popup.get_by_role('button',name='Prepare with ChatGPT',exact=True).click()
  end=time.monotonic()+5;preparation=None
  while time.monotonic()<end:
   preparation=next((p for p in self.context.pages if p.url.startswith(self.origin+'/jobs/1574/apply')),None)
   if preparation:break
   self.page.wait_for_timeout(40)
  self.assertIsNotNone(preparation);preparation.get_by_role('button',name='Use simulated ChatGPT return').click()
  self.badge(LISTING,'1')
  popup.close();self.page.bring_to_front();popup=self.popup();expect(popup.locator('#job-entries')).to_contain_text('CV prepared')
  with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now').click()
  application=event.value;application.wait_for_url(ATS);self.badge(ATS,'1');popup.close();self.page=application;popup=self.popup();self.fill(popup)
  self.assertIn('Radius job 1574',self.page.locator('#letter').input_value());self.assertIn('research projects',self.page.locator('#why').input_value());self.assertEqual(self.page.evaluate('window.submits'),0)
  bindings=self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault.bindings")
  self.assertTrue(any(b['job_id']==1574 and b['url']==ATS for b in bindings))
 def test_cv_version_refresh_invalidates_ready_badge(self):
  self.connect();self.page.goto(self.origin+'/jobs/1574/apply?profile_id=1');self.page.get_by_role('button',name='Use simulated ChatGPT return').click()
  source=self.context.new_page();source.goto(LISTING);self.badge(LISTING,'1')
  self.server.state['cv_version']='cv2'
  options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html');options.locator('#radius-refresh').click();expect(options.locator('#radius-status')).to_contain_text('2 jobs cached');self.badge(LISTING,'•')
 def test_old_draft_reload_does_not_rebind_after_reusable_facts_change(self):
  self.connect();self.page.goto(self.origin+'/jobs/1574/apply?profile_id=1');self.page.get_by_role('button',name='Use simulated ChatGPT return').click()
  source=self.context.new_page();source.goto(LISTING);self.badge(LISTING,'1')
  before=self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault.packages['1574:1'].profile_stamp")
  self.worker.evaluate("async()=>{const {applicant}=await chrome.storage.local.get('applicant');applicant.first_name='Updated';await chrome.storage.local.set({applicant});}")
  self.badge(LISTING,'•');self.page.reload();self.page.wait_for_timeout(400)
  after=self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault.packages['1574:1'].profile_stamp")
  self.assertEqual(before,after);self.badge(LISTING,'•')
 def test_direct_return_link_import_is_saved_per_job_with_its_application_url(self):
  from .journey_demo import package
  self.connect();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
  options.locator('#package-input').fill(json.dumps(package(1574,'cv1')));options.locator('#application_url').fill(ATS)
  options.get_by_role('button',name='Connect sources',exact=True).click();expect(options.locator('#source-notes')).to_contain_text('Radius job 1574')
  options.get_by_role('button',name='Save profile',exact=True).click();expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
  self.page.goto(ATS);self.badge(ATS,'1')
 def test_capture_cannot_roll_back_a_concurrently_updated_cv_version(self):
  from threading import Event
  from .journey_demo import package
  self.connect();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
  started,release=Event(),Event();self.server.state['hold_profiles']=(started,release)
  options.evaluate("pack=>{window.importResult=chrome.runtime.sendMessage({type:'import-package',input:JSON.stringify(pack)});}",package(1574,'cv1'))
  self.assertTrue(started.wait(3))
  self.worker.evaluate("async()=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.settings.cv_updated_at='cv2';v.cv_observed_at=Date.now()+100;await chrome.storage.local.set({radiusVault:v});}")
  release.set();result=options.evaluate('()=>window.importResult')
  self.assertFalse(result['ok']);self.assertIn('CV changed',result['error'])
  state=self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault")
  self.assertEqual(state['settings']['cv_updated_at'],'cv2');self.assertNotIn('1574:1',state['packages'])
 def test_radius_original_cv_fallback_supplies_missing_contact_on_prepared_fill(self):
  self.applicant['email']='';self.save_example();self.connect()
  self.page.goto(self.origin+'/jobs/1574/apply?profile_id=1')
  self.page.evaluate('''()=>{const field=document.createElement('textarea');field.setAttribute('aria-label','Prepared application prompt');field.value='SOURCE PAYLOAD (quoted data):'+JSON.stringify({original_cv_text:'Alex Example. Contact: alex@example.invalid'});document.body.append(field);}''')
  self.page.get_by_role('button',name='Use simulated ChatGPT return').click()
  source=self.context.new_page();source.goto(LISTING);self.badge(LISTING,'1');self.page=source;popup=self.popup()
  with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now').click()
  application=event.value;application.wait_for_url(ATS);self.badge(ATS,'1');popup.close();self.page=application;popup=self.popup();self.fill(popup)
  self.assertEqual(self.page.locator('#email').input_value(),'alex@example.invalid')
 def test_earlier_refresh_cannot_overwrite_a_later_profile_selection(self):
  from threading import Event
  self.connect();self.server.state['profiles']=[{'id':1,'name':'Research profile','cv':{'updated_at':'cv1'}},{'id':2,'name':'Second profile','cv':{'updated_at':'second-cv'}}]
  options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
  started,release=Event(),Event();self.server.state['hold_jobs']=(started,release)
  options.evaluate("()=>{window.oldSync=chrome.runtime.sendMessage({type:'refresh-radius'});}")
  self.assertTrue(started.wait(3))
  selected=options.evaluate("()=>chrome.runtime.sendMessage({type:'select-profile',profileId:2})")
  self.assertTrue(selected['ok']);release.set();old=options.evaluate('()=>window.oldSync');self.assertFalse(old['ok'])
  actual=self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault.settings")
  self.assertEqual(actual['profile_id'],2);self.assertEqual(actual['cv_updated_at'],'second-cv')
 def test_rejected_editor_package_never_persists_in_the_standalone_profile(self):
  from .journey_demo import package
  self.connect();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
  options.locator('#package-input').fill(json.dumps(package(1574,'cv1')));options.locator('#application_url').fill(ATS)
  options.get_by_role('button',name='Connect sources',exact=True).click();expect(options.locator('#source-notes')).to_contain_text('Radius job 1574')
  self.server.state['cv_version']='cv2';options.get_by_role('button',name='Save profile',exact=True).click()
  expect(options.locator('#status')).to_contain_text('could not be connected')
  application=self.worker.evaluate("async ()=>(await chrome.storage.local.get('applicant')).applicant.application")
  self.assertEqual(application['cover_letter'],'');self.assertEqual(application['application_answers'],'');self.assertIsNone(application['job_id']);self.assertEqual(application['url'],'')
 def test_return_hash_preserves_job_bullets_when_radius_rebuilds_the_standard_draft(self):
  from urllib.parse import quote
  from .journey_demo import package
  from .structured_cases import CASES
  self.connect();pack=package(1574,'cv1')
  self.page.goto(self.origin+'/jobs/1574/apply?profile_id=1#application='+quote(json.dumps(pack),safe=''))
  source=self.context.new_page();source.goto(LISTING);self.badge(LISTING,'1')
  self.page.wait_for_timeout(1700)
  saved=self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault.packages['1574:1'].package")
  self.assertEqual(saved.get('experience_bullets'),pack['experience_bullets'])
  self.context.route(ATS,lambda r:r.fulfill(content_type='text/html',body=CASES['history-month']))
  self.page=source;popup=self.popup()
  with self.context.expect_page() as event:self.page.get_by_role('link',name='Apply now').click()
  application=event.value;application.wait_for_url(ATS);self.badge(ATS,'1');popup.close();self.page=application;popup=self.popup();self.fill(popup)
  self.assertEqual(self.page.locator('#work textarea').first.input_value(),'Coordinated research milestones and partner workshops.')
 def test_disconnect_cancels_inflight_sync_and_clears_prepared_state(self):
  from threading import Event
  self.connect();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
  started,release=Event(),Event();self.server.state['hold_jobs']=(started,release)
  options.evaluate("()=>{window.pendingSync=chrome.runtime.sendMessage({type:'refresh-radius'});}")
  self.assertTrue(started.wait(3))
  response=options.evaluate("()=>chrome.runtime.sendMessage({type:'disconnect-radius'})")
  self.assertTrue(response['ok']);release.set()
  pending=options.evaluate('()=>window.pendingSync');self.assertFalse(pending['ok'])
  state=self.worker.evaluate("async ()=>(await chrome.storage.local.get('radiusVault')).radiusVault")
  self.assertIsNone(state['settings']);self.assertEqual(state['jobs'],[]);self.assertEqual(state['packages'],{})

 def test_refresh_preserves_a_prepared_job_outside_the_new_catalog_window(self):
  from .journey_demo import package
  self.connect();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
  imported=options.evaluate("pack=>chrome.runtime.sendMessage({type:'import-package',input:JSON.stringify(pack)})",package(1574,'cv1'))
  self.assertTrue(imported['ok'])
  self.server.state['catalog_jobs']=[{'id':2000,'title':'Other role','company_name':'Research Labs','url':OTHER,'is_active':True}]
  refreshed=options.evaluate("()=>chrome.runtime.sendMessage({type:'refresh-radius'})")
  self.assertTrue(refreshed['ok']);options.close()
  self.page.goto(LISTING);self.badge(LISTING,'1')

 def test_direct_package_import_keeps_the_catalog_bounded(self):
  from .journey_demo import package
  self.connect();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
  self.worker.evaluate("""async()=>{const {radiusVault:v}=await chrome.storage.local.get('radiusVault');v.jobs=Array.from({length:2000},(_,i)=>({id:10000+i,title:'Cached role',company_name:'Labs',url:`https://example.org/jobs/${10000+i}`,active:true}));await chrome.storage.local.set({radiusVault:v});}""")
  imported=options.evaluate("pack=>chrome.runtime.sendMessage({type:'import-package',input:JSON.stringify(pack)})",package(1574,'cv1'))
  self.assertTrue(imported['ok'])
  jobs=self.worker.evaluate("async()=>(await chrome.storage.local.get('radiusVault')).radiusVault.jobs")
  self.assertLessEqual(len(jobs),2000)
  self.assertTrue(any(job['id']==1574 for job in jobs))

 def test_disconnecting_during_search_cannot_return_the_old_cached_roles(self):
  self.connect();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
  self.worker.evaluate('''()=>{
    const original=globalThis.fetch;
    globalThis.fetch=async(input,options)=>{
      if(String(input).includes('/jobs/search?')) {
        globalThis.searchStarted=true;
        await new Promise(resolve=>{globalThis.releaseSearch=resolve;});
      }
      return original(input,options);
    };
  }''')
  options.evaluate("()=>{window.pendingSearch=chrome.runtime.sendMessage({type:'search-jobs',query:'Research'});}")
  deadline=time.monotonic()+5
  while not self.worker.evaluate('!!globalThis.searchStarted') and time.monotonic()<deadline:self.page.wait_for_timeout(20)
  self.assertTrue(self.worker.evaluate('!!globalThis.searchStarted'))
  disconnected=options.evaluate("()=>chrome.runtime.sendMessage({type:'disconnect-radius'})")
  self.assertTrue(disconnected['ok'])
  self.worker.evaluate('globalThis.releaseSearch()')
  result=options.evaluate('()=>window.pendingSearch')
  self.assertFalse(result['ok'])
