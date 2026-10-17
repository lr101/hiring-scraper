import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect
from .test_extension import ExtensionHarness, EXTENSION
from .application_cases import CASES

BASE = 'https://forms.example/'


class BestEffortBrowserTests(ExtensionHarness):
    def route(self, route):
        url = route.request.url
        if url.startswith(BASE):
            path=urlsplit(url).path.strip('/')
            body='<title>Radius prepared application</title><div class="application-cv-ready">Original CV</div>' if path.startswith('jobs/') else CASES[path]
            route.fulfill(content_type='text/html',body=body)
        else:
            super().route(route)

    def setUp(self):
        super().setUp()
        self.applicant.update(city='Heidelberg',country='Germany',phone='+49123456789',linkedin='https://www.linkedin.com/in/example')

    def open_case(self, case):
        self.page.goto(BASE+case)
        return self.popup()

    def test_native_en_reports_actual_required_missing_fields_in_popup(self):
        self.save_example(); popup=self.open_case('native-en'); self.fill(popup)
        self.assertEqual(self.page.locator('#country').input_value(),'DE')
        self.assertEqual(self.page.evaluate('window.draft.city'),'Heidelberg')
        report=self.report()
        missing={f['label']:f for f in report['unresolved']}
        self.assertEqual(set(missing),{'Salary expectation','Resume','Privacy consent'})
        self.assertTrue(missing['Salary expectation']['required'])
        expect(popup.locator('#remaining')).to_contain_text('Salary expectation (required)')
        self.assertNotIn(self.applicant['email'],json.dumps(report))
        self.assertEqual(self.page.evaluate('window.draft.submissions'),0)
        self.fill(popup);self.assertEqual(self.report()['filled'],0)

    def test_german_labels_and_country_options(self):
        self.save_example();popup=self.open_case('native-de');self.fill(popup)
        self.assertEqual(self.page.locator('#first').input_value(),'Alex')
        self.assertEqual(self.page.locator('#country').input_value(),'de')
        self.assertEqual(self.page.locator('#phone').input_value(),self.applicant['phone'])
        self.assertEqual([f['label'] for f in self.report()['unresolved']],['Postleitzahl'])

    def test_personio_names_and_required_label_suffixes(self):
        self.save_example();popup=self.open_case('personio-style');self.fill(popup)
        self.assertEqual(self.page.locator('#first').input_value(),'Alex')
        self.assertEqual(self.page.locator('#last').input_value(),'Example')
        self.assertEqual(self.page.locator('#phone').input_value(),self.applicant['phone'])
        self.assertEqual({f['label'] for f in self.report()['unresolved']},{'Academic title','Nationality'})

    def test_connects_cv_and_radius_package_in_editor_and_scopes_answers(self):
        options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.get_by_role('button',name='Load fictional example').click()
        options.locator('#cv_text').fill('Original CV example. alex@example.invalid')
        pack={'format':'radius-application-v1','job_id':9,'profile_id':2,'cv_updated_at':'v1','cv':'Known CV','cover_letter':'A job-specific cover letter.','application_answers':'Q: Why this role?\nA: My product coordination experience matches the role.\nQ: Availability?\nA: Two weeks','review_notes':'Personalized.'}
        options.locator('#package-input').fill(json.dumps(pack));options.locator('#application_url').fill(BASE+'lever-style')
        options.get_by_role('button',name='Connect sources',exact=True).click()
        expect(options.locator('#source-notes')).to_contain_text('Radius job 9')
        options.get_by_role('button',name='Save profile',exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        popup=self.open_case('lever-style');self.fill(popup)
        self.assertEqual(self.page.locator('#why').input_value(),'My product coordination experience matches the role.')
        self.assertEqual(self.page.locator('#letter').input_value(),pack['cover_letter'])
        self.assertEqual(self.page.locator('#availability').input_value(),'Two weeks')
        popup.close();self.page.goto(BASE+'lever-style?other-job=1');popup=self.popup();self.fill(popup)
        self.assertEqual(self.page.locator('#letter').input_value(),'')
        self.assertEqual(self.page.locator('#why').input_value(),'')
        self.assertTrue(any('unlinked' in f['reason'] for f in self.report()['unresolved']))

    def test_custom_combobox_selects_exact_option_and_updates_state(self):
        self.save_example();popup=self.open_case('custom-combobox');self.fill(popup)
        self.assertEqual(self.page.evaluate('window.draft.selectedCity'),'Heidelberg')
        self.assertEqual(self.page.locator('#city').get_attribute('aria-expanded'),'false')
        self.assertEqual(self.report()['unresolved'],[])

    def test_unverified_custom_selection_is_marked_changed_and_stops_later_fields(self):
        self.save_example();popup=self.open_case('custom-combobox')
        self.page.evaluate('''document.addEventListener('click',event=>{if(event.target.getAttribute('role')==='option'){
          const input=document.querySelector('#city');input.value='Unverified selection';input.setAttribute('aria-expanded','true');
        }})''')
        self.fill(popup);report=self.report()
        self.assertEqual(next(f for f in report['fields'] if f['id']=='city')['status'],'changed_unverified')
        self.assertTrue(report['partial_fill'])
        self.assertEqual(self.page.locator('#country').input_value(),'')

    def test_existing_matching_but_invalid_value_is_not_reported_resolved(self):
        self.save_example();popup=self.open_case('validation-rejection')
        self.page.locator('#email').fill(self.applicant['email'])
        self.page.locator('#email').evaluate("el=>el.setCustomValidity('server validation still required')")
        self.fill(popup)
        self.assertEqual(next(f for f in self.report()['fields'] if f['id']=='email')['status'],'rejected')

    def test_conflicts_ambiguity_readonly_and_unsupported_sections(self):
        self.save_example();popup=self.open_case('ambiguous-and-embedded');self.fill(popup)
        self.assertEqual(self.page.locator('#first').input_value(),'User correction')
        fields=self.report()['fields']; statuses={f['status'] for f in fields}
        self.assertTrue({'conflict','ambiguous','disabled','unsupported','manual'} <= statuses, fields)
        self.assertEqual(self.page.locator('#email').input_value(),'')
        self.assertFalse(self.page.locator('#consent').is_checked())
        self.assertTrue(any(f['label']=='External experience editor' for f in fields))
        self.assertTrue(any(f['label']=='Custom education editor' for f in fields))

    def test_site_rejection_does_not_prevent_other_fields_filling(self):
        self.save_example();popup=self.open_case('validation-rejection');self.fill(popup)
        self.assertEqual(self.page.locator('#first').input_value(),'Alex')
        self.assertEqual(self.page.locator('#email').input_value(),self.applicant['email'])
        self.assertEqual(self.page.locator('#phone').input_value(),'')
        self.assertEqual(next(f for f in self.report()['fields'] if f['id']=='phone')['status'],'rejected')


    def test_paired_fictional_cv_and_actual_dummy_compressed_link(self):
        options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.get_by_role('button',name='Load paired fictional CV and return link',exact=True).click()
        expect(options.locator('#first_name')).to_have_value('Mara')
        expect(options.locator('#package-input')).to_have_value(__import__('re').compile(r'.*#applicationz=.+'))
        options.locator('#application_url').fill(BASE+'lever-style')
        options.get_by_role('button',name='Connect sources',exact=True).click()
        expect(options.locator('#source-notes')).to_contain_text('Radius job 1574')
        options.get_by_role('button',name='Save profile',exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        popup=self.open_case('lever-style');self.fill(popup)
        self.assertEqual(self.page.locator('#name').input_value(),'Mara Neumann')
        self.assertIn('Forschungsprojektsteuerung',self.page.locator('#why').input_value())
        self.assertIn('Mara Neumann',self.page.locator('#letter').input_value())
        self.assertEqual(self.page.locator('#availability').input_value(),'')

    def test_imports_selected_radius_draft_without_scanning_other_storage(self):
        self.applicant=json.loads((EXTENSION/'dummy-cv.json').read_text());self.save_example()
        pack=json.loads((EXTENSION/'dummy-package.json').read_text())
        self.page.goto(BASE+'jobs/1574/apply?profile_id=1')
        self.page.evaluate('''pack=>localStorage.setItem('radius:application-draft:v1:1574:1',JSON.stringify({package:pack,context:{job_id:1574,profile_id:1,cv_updated_at:pack.cv_updated_at},saved_at:Date.now()}))''',pack)
        popup=self.popup()
        expect(popup.locator('#page-name')).to_have_text('Prepared Radius application')
        popup.get_by_role('button',name='Connect ChatGPT result',exact=True).click()
        expect(popup.locator('#status')).to_contain_text('ChatGPT result connected')
        saved=self.worker.evaluate("async ()=>(await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(saved['first_name'],'Mara')
        self.assertEqual(saved['application']['job_id'],1574)
        self.assertEqual(saved['application']['url'],'')
    def test_password_document_fallback_and_mixed_forms_are_not_filled(self):
        self.save_example()
        for html in ['<label>Email<input id=email type=email></label><label>Phone<input id=phone type=tel></label><input type=password>', '<form><label>Email<input id=news type=email></label><label>Phone<input type=tel></label><button>Subscribe</button></form><form><h1>Job application</h1><label>First name<input id=first></label><label>Email<input id=email type=email></label><button>Submit application</button></form>']:
            self.context.route(BASE+'safety',lambda route,request,body=html:route.fulfill(content_type='text/html',body=body))
            self.page.goto(BASE+'safety');popup=self.popup()
            button=popup.locator('#fill')
            if button.is_enabled():self.fill(popup)
            self.assertEqual(self.page.locator('input[type=email]').first.input_value(),'')
            popup.close()
