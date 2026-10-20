import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect
from .test_extension import ExtensionHarness, EXTENSION
from .application_cases import CASES

BASE = 'https://forms.example/'


class BestEffortBrowserTests(ExtensionHarness):
    def check_expanded_case(self, name):
        from experiments.autofill_corpus import cases
        case=next(case for case in cases() if case.name==name)
        self.applicant=case.profile
        self.context.route(BASE+'expanded',lambda route:route.fulfill(content_type='text/html',body=case.html))
        self.save_example();self.page.goto(BASE+'expanded');popup=self.popup();self.fill(popup)
        for identifier,value in case.expected.items():
            actual=self.page.locator('#'+identifier).is_checked() if isinstance(value,bool) else self.page.locator('#'+identifier).input_value()
            self.assertEqual(actual,value,(name,identifier))
        self.fill(popup)
        for identifier,value in case.expected.items():
            actual=self.page.locator('#'+identifier).is_checked() if isinstance(value,bool) else self.page.locator('#'+identifier).input_value()
            self.assertEqual(actual,value,(name,identifier,'repeat'))
        popup.close()

    def test_explicit_contact_confirmation_fields_reuse_the_saved_fact(self):
        for name in ['confirmation-0-wrapped','confirmation-2-aria','confirmation-4-labelledby','confirmation-6-wrapped']:
            with self.subTest(name=name):self.check_expanded_case(name)

    def test_foreign_plain_div_headings_do_not_receive_applicant_contacts(self):
        for name in ['foreign-Reference-div-heading','foreign-Emergency contact-div-heading','foreign-Education-div-heading']:
            with self.subTest(name=name):self.check_expanded_case(name)

    def test_changed_question_context_is_rechecked_before_later_fields_are_filled(self):
        self.check_expanded_case('conditional-scope-fieldset')

    def test_conversational_contact_wording_and_bounded_preference_questions(self):
        for name in ['wording-15-wrapped','wording-17-aria','wording-22-labelledby','wording-50-adjacent']:
            with self.subTest(name=name):self.check_expanded_case(name)

    def test_brand_names_are_not_broken_by_camel_case_identifier_normalization(self):
        for name in ['wording-41-wrapped','wording-42-aria','wording-43-labelledby']:
            with self.subTest(name=name):self.check_expanded_case(name)

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

    def open_markup(self, markup):
        self.context.route(BASE+'adaptive', lambda route: route.fulfill(
            content_type='text/html', body='<meta charset="utf-8"><form><h1>Application</h1>'+markup+'</form>'))
        self.save_example()
        self.page.goto(BASE+'adaptive')
        return self.popup()

    def test_adaptive_contact_signals_and_conversational_english_german(self):
        # Generic labels, opaque identifiers and conversational wording must all
        # resolve the applicant fact, without borrowing unrelated nearby text.
        for markup, expected in [
            ('<label>Name * (required)<input id=target></label>', 'Alex Example'),
            ('<label>Bitte geben Sie Ihren Vornamen ein<input id=target></label>', 'Alex'),
            ('<label>Where do you currently live?<input id=target></label>', 'Heidelberg'),
            ('<span id=question>In welcher Stadt wohnen Sie?</span><input id=target aria-labelledby=question>', 'Heidelberg'),
            ('<input id=target name="applicant[fullName]" aria-label="Your answer">', 'Alex Example'),
            ('<input id=target name="candidate[address][postalCode]">', '69115'),
            ('<label>Mobile number<input id=target></label>', '+49123456789'),
            ('<label>Straße und Hausnummer<input id=target></label>', 'Example Street 1'),
            ('<label>When can you start?<input id=target></label>', '2026-12-01'),
            ('<label>What is your full name?<input id=target></label>', 'Alex Example'),
            ('<label>Vor- und Nachname<input id=target></label>', 'Alex Example'),
            ('<div><label>Vorname</label><input id=target></div>', 'Alex'),
            ('<label>Ihre Telefonnummer (Pflichtfeld)<input id=target></label>', '+49123456789'),
            ('<label>Where are you currently located?<input id=target></label>', 'Heidelberg'),
            ('<input id=target autocomplete="section-applicant shipping street-address">', 'Example Street 1'),
            ('<label>ZIP / Postal code<input id=target></label>', '69115'),
            ('<label>What is your country of residence?<input id=target></label>', 'Germany'),
        ]:
            with self.subTest(markup=markup):
                self.applicant.update(postal_code='69115', street='Example Street 1', preferences={'available_from':'2026-12-01'})
                popup=self.open_markup('<label>Email<input type=email></label><input type=file>'+markup)
                self.fill(popup)
                self.assertEqual(self.page.locator('#target').input_value(),expected)
                popup.close()

    def test_conflicting_signals_and_unrelated_name_questions_remain_unfilled(self):
        popup=self.open_markup('''<label>Email<input type=email></label><label>Phone<input type=tel></label>
          <label>Company name<input id=company name=name></label>
          <label>Last name<input id=conflict autocomplete=given-name></label>
          <label>Where would you like to live?<input id=future></label>
          <label>Name of your reference<input id=reference name=fullName></label>
          <div><label>Vorname</label><input id=adjacent><input></div>
          <label for=elsewhere>Vorname</label><input id=other>
          <input id=placeholder name=fullName placeholder="Name of your reference">
          <input id=company_name name=fullName placeholder="Your answer">''')
        self.fill(popup)
        for target in ['company','conflict','future','reference','adjacent','other','placeholder','company_name']:
            self.assertEqual(self.page.locator('#'+target).input_value(),'')

    def test_radio_question_context_and_saved_text_choices(self):
        self.applicant['preferences']={'willing_to_work_onsite':True,'employment_type':'Part-time','willing_to_relocate':False}
        popup=self.open_markup('''<label>Email<input type=email></label><label>Full name<input></label>
          <div role=radiogroup aria-labelledby=onsite><p id=onsite>Are you willing to work on-site?</p>
            <label><input id=onsite-yes type=radio name=q1 value=1>Ja</label>
            <label><input type=radio name=q1 value=0>Nein</label></div>
          <fieldset><legend>Employment type</legend><label><input type=radio name=q2 value=full>Full-time</label>
            <label><input id=part type=radio name=q2 value=part>Part-time</label></fieldset>
          <div class=application-question><p class=application-label>Sind Sie umzugsbereit?</p>
            <label><input type=radio name=q3>Ja</label><label><input id=relocate-no type=radio name=q3>Nein</label></div>''')
        self.fill(popup)
        for target in ['onsite-yes','part','relocate-no']:
            self.assertTrue(self.page.locator('#'+target).is_checked(),target)
        self.assertEqual(self.report()['unresolved'],[])
        self.fill(popup)
        self.assertEqual(self.report()['filled'],0)

    def test_radio_unknown_preference_conflicts_and_disabled_options_are_preserved(self):
        self.applicant['preferences']={'willing_to_work_onsite':True,'employment_type':'Part-time'}
        popup=self.open_markup('''<label>Email<input type=email></label><label>Name<input></label>
          <fieldset><legend>Working on site</legend><label><input type=radio name=q1>Yes</label>
            <label><input id=existing type=radio name=q1 checked>No</label></fieldset>
          <fieldset><legend>Employment type</legend><label><input id=disabled type=radio name=q2 disabled>Part-time</label>
            <label><input type=radio name=q2>Full-time</label></fieldset>
          <fieldset><legend>Willing to relocate</legend><label><input id=unknown type=radio name=q3>Yes</label>
            <label><input type=radio name=q3>No</label></fieldset>
          <label><input id=consent type=checkbox>Privacy consent</label>''')
        self.fill(popup)
        self.assertTrue(self.page.locator('#existing').is_checked())
        for target in ['disabled','unknown','consent']:
            self.assertFalse(self.page.locator('#'+target).is_checked())
        self.assertTrue({'conflict','disabled','manual'} <= {f['status'] for f in self.report()['fields']})

    def test_localized_employment_choices_and_onsite_phrasings(self):
        self.applicant['preferences']={'willing_to_work_onsite':False, 'employment_type':'Part-time'}
        popup=self.open_markup('''<label>Email<input type=email></label><label>Name<input></label>
          <fieldset><legend>Are you comfortable working on-site?</legend>
            <label><input type=radio name=q1>Yes, I am</label><label><input id=no type=radio name=q1>No, I am not</label></fieldset>
          <label>Employment type<select id=employment><option value="">Bitte wählen</option><option value=pt>Teilzeit</option><option>Vollzeit</option></select></label>''')
        self.fill(popup)
        self.assertTrue(self.page.locator('#no').is_checked())
        self.assertEqual(self.page.locator('#employment').input_value(),'pt')

    def test_options_editor_saves_onsite_answer_and_unknown(self):
        self.save_example()
        options=self.context.new_page();options.goto(f'chrome-extension://{self.extension_id}/options.html')
        for value in ['false', 'true', '']:
            options.locator('#willing_to_work_onsite').select_option(value)
            options.get_by_role('button', name='Save profile', exact=True).click()
            expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
            options.reload()
            expect(options.locator('#willing_to_work_onsite')).to_have_value(value)

    def test_reference_placeholder_and_company_identifier_override_generic_name_hints(self):
        for markup in [
            '<input id=target name=fullName placeholder="Name of your reference">',
            '<input id=target name="company[name]" autocomplete=name>',
        ]:
            with self.subTest(markup=markup):
                popup=self.open_markup('<label>Email<input type=email></label><label>Phone<input type=tel></label>'+markup)
                self.fill(popup)
                self.assertEqual(self.page.locator('#target').input_value(),'')
                popup.close()

    def test_duplicate_text_radio_questions_are_not_filled_from_one_preference(self):
        self.applicant['preferences']={'employment_type':'Part-time'}
        popup=self.open_markup('<label>Email<input type=email></label><label>Name<input></label>'+''.join(
            f'<fieldset><legend>Employment type</legend><label><input type=radio name=q{i}>Part-time</label><label><input type=radio name=q{i}>Full-time</label></fieldset>'
            for i in range(2)))
        self.fill(popup)
        self.assertEqual(self.page.locator('input[type=radio]:checked').count(),0)
        self.assertEqual([f['status'] for f in self.report()['fields'] if f['label']=='Employment type'],['ambiguous','ambiguous'])

    def test_reused_native_radio_name_does_not_overwrite_choice_in_another_wrapper(self):
        self.applicant['preferences']={'willing_to_work_onsite':True, 'willing_to_relocate':False}
        popup=self.open_markup('''<label>Email<input type=email></label><label>Name<input></label>
          <fieldset><legend>Working on site</legend><label><input id=existing type=radio name=shared checked>Yes</label><label><input type=radio name=shared>No</label></fieldset>
          <fieldset><legend>Willing to relocate</legend><label><input type=radio name=shared>Yes</label><label><input id=other-no type=radio name=shared>No</label></fieldset>''')
        self.fill(popup)
        self.assertTrue(self.page.locator('#existing').is_checked())
        self.assertFalse(self.page.locator('#other-no').is_checked())

    def test_native_contact_types_still_identify_unrecognized_contact_prompts(self):
        for field_type, expected in [('email', self.applicant['email']), ('tel', self.applicant['phone'])]:
            with self.subTest(field_type=field_type):
                popup=self.open_markup('<label>First name<input></label><label>Last name<input></label>'+f'<label>How can we contact you?<input id=target type={field_type}></label>')
                self.fill(popup)
                self.assertEqual(self.page.locator('#target').input_value(),expected)
                popup.close()

    def test_section_context_keeps_other_people_and_history_out_of_contact_fields(self):
        for heading in ['Reference', 'Emergency contact', 'Work experience', 'Education', 'Referenzperson', 'Notfallkontakt', 'Berufserfahrung', 'Ausbildung']:
            with self.subTest(heading=heading):
                popup=self.open_markup(f'''<label>First name<input id=applicant></label><label>Email<input id=email type=email></label>
                  <fieldset><legend>{heading}</legend><label>Name<input id=other-name autocomplete=name></label>
                    <label>Email<input id=other-email type=email></label><label>City<input id=other-city></label></fieldset>''')
                self.fill(popup)
                self.assertEqual(self.page.locator('#applicant').input_value(),'Alex')
                self.assertEqual(self.page.locator('#email').input_value(),self.applicant['email'])
                for target in ['other-name','other-email','other-city']:
                    self.assertEqual(self.page.locator('#'+target).input_value(),'')
                popup.close()

    def test_aria_and_section_headings_identify_reference_controls(self):
        for wrapper in [
            '<div role=group aria-labelledby=heading><p id=heading>Reference contact</p>{fields}</div>',
            '<section><h2>Emergency contact</h2>{fields}</section>',
            '<fieldset><legend>Application</legend><section aria-label=References>{fields}</section></fieldset>',
        ]:
            with self.subTest(wrapper=wrapper):
                fields='<label>Name<input id=other-name></label><label>Email<input id=other-email type=email></label>'
                popup=self.open_markup('<label>First name<input id=applicant></label><label>Email<input id=email type=email></label>'+wrapper.format(fields=fields))
                self.fill(popup)
                self.assertEqual(self.page.locator('#applicant').input_value(),'Alex')
                self.assertEqual(self.page.locator('#email').input_value(),self.applicant['email'])
                self.assertEqual(self.page.locator('#other-name').input_value(),'')
                self.assertEqual(self.page.locator('#other-email').input_value(),'')
                popup.close()

    def test_application_heading_does_not_make_applicant_contacts_into_history(self):
        for heading in ['Employment application','Education application','Academic application']:
            with self.subTest(heading=heading):
                popup=self.open_markup(f'''<label>City<input></label><input type=file><section><h2>{heading}</h2>
                  <label>First name<input id=applicant></label><label>Email<input id=email type=email></label></section>''')
                self.fill(popup)
                self.assertEqual(self.page.locator('#applicant').input_value(),'Alex')
                self.assertEqual(self.page.locator('#email').input_value(),self.applicant['email'])
                popup.close()
