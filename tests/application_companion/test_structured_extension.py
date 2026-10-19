import json
from urllib.parse import urlsplit
from playwright.sync_api import expect
from .test_extension import ExtensionHarness, EXTENSION
from .structured_cases import CASES

BASE='https://structured.example/'
class StructuredBrowserTests(ExtensionHarness):
    def route(self,route):
        if route.request.url.startswith(BASE):route.fulfill(content_type='text/html',body=CASES[urlsplit(route.request.url).path.strip('/')])
        else:super().route(route)
    def setUp(self):
        super().setUp()
        self.applicant.update(website='https://example.invalid',preferences={'available_from':'2026-12-01','notice_period':'One month','salary_amount':'65000','salary_currency':'EUR','salary_period':'year','authorization_country':'Germany','authorized_to_work':True,'sponsorship_required':False,'willing_to_relocate':False,'weekly_hours':'20','employment_type':'Part-time'})
        for i,e in enumerate(self.applicant['experiences']):e.update(id=f'job-{i+1}',location=['Heidelberg, Germany','Berlin, Germany','Remote'][i])
    def open_case(self,name):self.page.goto(BASE+name);return self.popup()
    def test_explicit_common_answers_fill_text_select_and_radio_without_consent(self):
        self.save_example();popup=self.open_case('common');self.fill(popup)
        self.assertEqual(self.page.locator('#salary').input_value(),'65000')
        self.assertEqual(self.page.locator('#available').input_value(),'2026-12-01')
        self.assertEqual(self.page.locator('#employment').input_value(),'Part-time')
        self.assertTrue(self.page.locator('#auth-yes').is_checked())
        self.assertEqual(self.page.locator('#sponsor').input_value(),'false')
        self.assertEqual(self.page.locator('#relocate').input_value(),'n')
        self.assertFalse(self.page.locator('#consent').is_checked())
        self.assertEqual({f['label'] for f in self.report()['unresolved']},{'Resume','I accept the privacy policy'})
        self.assertEqual(self.page.evaluate('window.draft.submissions'),0)
        self.fill(popup);self.assertEqual(self.report()['filled'],0)
    def test_repeated_jobs_locations_dates_and_scoped_chatgpt_bullets(self):
        self.applicant['application']={'url':BASE+'history-month','experience_bullets':[{'experience_id':'job-1','bullets':['Coordinated three teams.','Reduced overdue work.']}]}
        self.save_example();popup=self.open_case('history-month');self.fill(popup)
        groups=self.page.locator('#work > fieldset');self.assertEqual(groups.count(),3)
        for i,e in enumerate(self.applicant['experiences']):
            self.assertEqual(groups.nth(i).locator('[name=company]').input_value(),e['company'])
            self.assertEqual(groups.nth(i).locator('[name=location]').input_value(),e['location'])
            self.assertEqual(groups.nth(i).locator('[name=start]').input_value(),e['start'])
        self.assertEqual(groups.first.locator('textarea').input_value(),'Coordinated three teams.\nReduced overdue work.')
        self.assertEqual(self.report()['unresolved'],[])
        self.assertEqual(self.report()['experiences_added'],2)
        self.fill(popup);self.assertEqual(self.report()['filled'],0);self.assertEqual(groups.count(),3)
        popup.close();popup=self.open_case('history-month?other=1');self.fill(popup)
        self.assertEqual(self.page.locator('#work textarea').first.input_value(),self.applicant['experiences'][0]['description'])
    def test_split_dates_fill_known_years_without_guessing_months(self):
        self.applicant['experiences'][1]['start']='2022'
        self.save_example();popup=self.open_case('history-split');self.fill(popup)
        self.assertEqual(self.page.locator('[name=start_year]').nth(1).input_value(),'2022')
        self.assertEqual(self.page.locator('[name=start_month]').nth(1).input_value(),'')
        self.assertEqual(self.page.locator('[name=start_month]').first.input_value(),self.applicant['experiences'][0]['start'][5:])
        missing=self.report()['unresolved'];self.assertEqual(len(missing),1);self.assertIn('month',missing[0]['reason'].lower())
    def test_conflicting_history_prevents_mixing_jobs_but_allows_contacts(self):
        self.save_example();popup=self.open_case('history-month')
        self.page.locator('[name=company]').fill('Different company')
        self.fill(popup)
        self.assertEqual(self.page.locator('[name=title]').input_value(),'')
        self.assertEqual(self.page.locator('#work > fieldset').count(),1)
        self.assertEqual(self.page.locator('#first').input_value(),'Alex')
        self.assertTrue(any(f['status']=='conflict' for f in self.report()['unresolved']))
    def test_salary_units_jurisdiction_and_nonunique_choices_stay_unfilled(self):
        self.save_example();popup=self.open_case('question-limits');self.fill(popup)
        for selector in ['#salary','#usd','#auth','#sponsor']:self.assertEqual(self.page.locator(selector).input_value(),'')
        reasons=' '.join(f['reason'] for f in self.report()['unresolved'])
        self.assertIn('currency',reasons);self.assertIn('country',reasons)
    def test_employer_specific_modal_is_reported_and_not_saved(self):
        self.save_example();popup=self.open_case('history-broken');self.fill(popup)
        self.assertEqual(self.page.locator('#work > fieldset').count(),1)
        self.assertTrue(any('inline' in f['reason'] for f in self.report()['unresolved']))
        self.assertEqual(self.page.evaluate('window.draft.submissions'),0)
    def test_editor_preserves_facts_and_generates_id_based_bullet_request(self):
        self.save_example();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
        expect(options.locator('#available_from')).to_have_value('2026-12-01')
        options.locator('[data-key=location]').first.fill('Karlsruhe, Germany')
        options.locator('#sponsorship_required').select_option('false')
        options.get_by_text('Ask ChatGPT for tailored work bullets',exact=True).click()
        options.get_by_role('button',name='Prepare bullet-point request',exact=True).click()
        self.assertIn('"experience_id": "job-1"',options.locator('#bullet-prompt').input_value())
        self.assertIn('Karlsruhe, Germany',options.locator('#bullet-prompt').input_value())
        options.get_by_role('button',name='Save profile',exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        options.reload();expect(options.locator('[data-key=location]').first).to_have_value('Karlsruhe, Germany')
        expect(options.locator('#sponsorship_required')).to_have_value('false')
    def test_unknown_answers_and_multiple_country_questions_are_not_guessed(self):
        self.applicant['preferences']['sponsorship_required']=None
        self.applicant['preferences']['authorized_to_work']=True
        self.save_example();popup=self.open_case('common')
        self.page.locator('fieldset legend').evaluate("el=>el.textContent='Are you authorized to work in Germany or the United States?'")
        self.fill(popup)
        self.assertFalse(self.page.locator('#auth-yes').is_checked())
        self.assertFalse(self.page.locator('#auth-no').is_checked())
        self.assertEqual(self.page.locator('#sponsor').input_value(),'')
    def test_current_role_does_not_silently_hide_an_existing_end_date(self):
        self.save_example();popup=self.open_case('history-month')
        self.page.locator('[name=end]').fill('2025-01')
        self.fill(popup)
        self.assertFalse(self.page.locator('[name=current]').first.is_checked())
        self.assertEqual(self.page.locator('[name=end]').first.input_value(),'2025-01')
        self.assertTrue(any(f['status']=='conflict' for f in self.report()['unresolved']))
    def test_changing_link_or_job_facts_clears_previous_tailored_package(self):
        self.applicant['application']={'url':BASE+'history-month','job_id':1574,'cover_letter':'Old letter','application_answers':'Q: Why?\nA: Old answer','experience_bullets':[{'experience_id':'job-1','bullets':['Old tailored work.']}]}
        for change in ('url','location'):
            self.save_example();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
            if change=='url':options.locator('#application_url').fill(BASE+'history-split')
            else:options.locator('[data-key=location]').first.fill('New location')
            options.get_by_role('button',name='Save profile',exact=True).click()
            expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
            saved=self.worker.evaluate("async ()=>(await chrome.storage.local.get('applicant')).applicant")
            self.assertEqual(saved['application']['experience_bullets'],[])
            self.assertEqual(saved['application']['cover_letter'],'')
            self.assertEqual(saved['application']['application_answers'],'')
            options.close()
    def test_existing_job_location_conflict_does_not_add_rows(self):
        self.save_example();popup=self.open_case('history-month');self.page.locator('[name=location]').fill('Existing different location');self.fill(popup)
        self.assertEqual(self.page.locator('#work > fieldset').count(),1)
        self.assertEqual(self.page.locator('[name=location]').input_value(),'Existing different location')
        self.assertEqual(self.page.locator('[name=company]').input_value(),'')
    def test_bare_location_question_is_not_assumed_to_be_home_city(self):
        self.applicant['city']='Heidelberg';self.save_example();popup=self.open_case('common')
        self.page.locator('#website').evaluate("el=>{el.parentElement.firstChild.textContent='Location';el.type='text'}")
        self.fill(popup);self.assertEqual(self.page.locator('#website').input_value(),'')
    def test_hash_routed_application_does_not_reuse_other_hash_bullets(self):
        self.applicant['application']={'url':BASE+'history-month#job-1','experience_bullets':[{'experience_id':'job-1','bullets':['Tailored for other hash.']}]}
        self.save_example();popup=self.open_case('history-month#job-2');self.fill(popup)
        self.assertEqual(self.page.locator('#work textarea').first.input_value(),self.applicant['experiences'][0]['description'])
    def test_first_link_binding_and_fresh_package_after_fact_edit_keep_valid_bullets(self):
        self.applicant['application']={'url':'','job_id':1574,'cover_letter':'Unbound letter','application_answers':'','experience_bullets':[{'experience_id':'job-1','bullets':['First valid bullet.']}]}
        self.save_example();options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#application_url').fill(BASE+'history-month')
        options.get_by_role('button',name='Save profile',exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        saved=self.worker.evaluate("async ()=>(await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(saved['application']['experience_bullets'][0]['bullets'],['First valid bullet.'])
        options.locator('[data-key=location]').first.fill('Updated city')
        replacement={'format':'radius-application-v1','job_id':1574,'profile_id':1,'cv_updated_at':'v2','cv':[],'cover_letter':'Fresh letter','application_answers':'','experience_bullets':[{'experience_id':'job-1','bullets':['Freshly prepared bullet.']}]}
        options.locator('#package-input').fill(json.dumps(replacement))
        options.get_by_role('button',name='Save profile',exact=True).click()
        expect(options.locator('#source-notes')).to_contain_text('Radius job 1574')
        saved=self.worker.evaluate("async ()=>(await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(saved['experiences'][0]['location'],'Updated city')
        self.assertEqual(saved['application']['experience_bullets'][0]['bullets'],['Freshly prepared bullet.'])
        self.assertEqual(saved['application']['cover_letter'],'Fresh letter')
