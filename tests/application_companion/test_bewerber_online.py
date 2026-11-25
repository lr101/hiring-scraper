"""Regression for the public Job Impulse form (2026-10-25), fictional facts only."""
from playwright.sync_api import expect
from .test_extension import ExtensionHarness

URL = 'https://www.bewerber-online.de/job-impulse/Jobs/227067/Produktmanager__m_w_d_/_ref=Arbeitsagentur'
# Preserve the live form's incorrect for= targets, datepicker and opaque options.
FORM = '''<!doctype html><html lang="de"><meta charset="utf-8"><title>Produktmanager Bewerbung</title>
<form>
<div class="form-group"><label for="anrede">Anrede:</label><select id="anrede" name="Anrede"><option value="">Bitte wählen</option><option value="1">Herr</option><option value="0">Frau</option></select></div>
<div class="form-group"><label for="vorname">Vorname:</label><input id="vorname" name="Vorname"></div>
<div class="form-group"><label for="nachname">Name:</label><input id="nachname" name="Name"></div>
<div class="form-group"><label for="geburtsname">Geburtsname:</label><input id="geburtsname" name="Geburtsname"></div>
<div class="form-group"><label for="geburtsdatum">Geburtsdatum:</label><input class="datepicker" id="geburtsdatum" name="Geburtsdatum"></div>
<div class="form-group"><label for="geburtsort">Geburtsort:</label><input id="geburtsort" name="Geburtsort"></div>
<div class="form-group"><label for="strasse">Straße:</label><input id="strasse" name="Strasse"></div>
<div class="form-group"><label for="Plz">PLZ:</label><input id="plz" name="Plz"></div>
<div class="form-group"><label for="strasse">Ort:</label><input id="ort" name="Ort"></div>
<div class="form-group"><label for="strasse">Staatsangehörigkeit:</label><select id="Kennung_Staatsangehoerigkeit" name="Kennung_Staatsangehoerigkeit"><option value="">Bitte wählen</option><option value="1">Deutschland</option><option value="10">Frankreich</option></select></div>
<div class="form-group"><label for="email">E-Mail:</label><input id="email" name="Email" type="email"></div>
<div class="form-group"><label for="verfuegbar_ab">Ab wann sind Sie verfügbar?</label><div><input class="datepicker" id="verfuegbar_ab" name="verfuegbar_ab"></div></div>
<div class="form-group"><label for="Email">Führerschein:</label><input id="Fuehrerschein" name="Fuehrerschein"></div>
<div class="form-group"><label for="Bemerkung">Möchten Sie uns noch weitere Informationen mitteilen?</label><textarea id="Bemerkung" name="Bemerkung"></textarea></div>
<label><input id="consent" type="checkbox">Ich stimme der Datenschutzerklärung zu</label>
<button type="submit">Bewerben</button></form>
<iframe id="iframe_similarJobs" src="/index.php?page=show_similarJobs.php"></iframe>
<script>window.submissions=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();window.submissions++}</script></html>'''


class BewerberOnlineTests(ExtensionHarness):
    def open_form(self):
        self.context.route(URL, lambda route: route.fulfill(content_type='text/html', body=FORM))
        self.context.route('**/index.php?*', lambda route: route.fulfill(content_type='text/html', body='<h2>Ähnliche Jobs</h2><a href="/jobs/2">Weiterer Job</a>'))
        self.save_example()
        self.page.goto(URL)
        return self.popup()

    def test_mislinked_labels_fill_each_address_component(self):
        self.applicant.update(street='Musterstraße 12', postal_code='10115', city='Berlin')
        popup = self.open_form()
        self.fill(popup)
        for key, value in {'strasse': 'Musterstraße 12', 'plz': '10115', 'ort': 'Berlin', 'email': 'alex@example.invalid'}.items():
            expect(self.page.locator('#' + key)).to_have_value(value)
        self.assertFalse(any(field['id'] == 'frame' for field in self.report()['fields']))
        self.assertEqual(self.page.evaluate('window.submissions'), 0)
        expect(self.page.locator('#consent')).not_to_be_checked()

    def test_explicit_personal_facts_and_local_dates_fill_without_consent(self):
        self.applicant.update(salutation='Herr', birth_name='Beispiel', birth_date='1992-02-29',
                              birth_place='Bonn', nationality='Germany', driving_licence='Klasse B',
                              additional_information='Kontakt bitte per E-Mail.', street='Musterstraße 12', postal_code='10115', city='Berlin')
        self.applicant['preferences'] = {'available_from': '2027-01-15'}
        popup = self.open_form()
        self.fill(popup)
        expected = {'anrede':'1', 'geburtsname':'Beispiel', 'geburtsdatum':'29.02.1992',
                    'geburtsort':'Bonn', 'Kennung_Staatsangehoerigkeit':'1', 'Fuehrerschein':'Klasse B',
                    'Bemerkung':'Kontakt bitte per E-Mail.', 'verfuegbar_ab':'15.01.2027'}
        for key, value in expected.items():
            expect(self.page.locator('#' + key)).to_have_value(value)
        self.fill(popup)
        for key, value in expected.items():
            expect(self.page.locator('#' + key)).to_have_value(value)
        expect(self.page.locator('#consent')).not_to_be_checked()
        self.assertEqual(self.page.evaluate('window.submissions'), 0)

    def test_missing_personal_facts_remain_blank_and_explain_how_to_supply_them(self):
        popup = self.open_form()
        self.fill(popup)
        for key in ['anrede', 'geburtsname', 'geburtsdatum', 'geburtsort', 'Kennung_Staatsangehoerigkeit', 'Fuehrerschein', 'Bemerkung']:
            expect(self.page.locator('#' + key)).to_have_value('')
            field = next(field for field in self.report()['fields'] if field['id'] == key)
            self.assertEqual(field['status'], 'missing')
            self.assertIn('profile', field['reason'].lower())

    def test_editor_retains_personal_facts(self):
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        for key, value in {'salutation':'Frau', 'birth_name':'Beispiel', 'birth_date':'1992-02-29', 'birth_place':'Bonn', 'nationality':'Deutschland', 'driving_licence':'Klasse B', 'additional_information':'Rückfragen per E-Mail.'}.items():
            options.locator('#' + key).fill(value)
        options.get_by_role('button', name='Save profile', exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        options.reload()
        expect(options.locator('#birth_date')).to_have_value('1992-02-29')
        expect(options.locator('#additional_information')).to_have_value('Rückfragen per E-Mail.')

    def test_exact_job_answers_still_fill_new_personal_questions_when_facts_are_blank(self):
        self.applicant['application'] = {'url': URL, 'application_answers': 'Q: Geburtsort:\nA: Bonn\nQ: Möchten Sie uns noch weitere Informationen mitteilen?\nA: Kontakt per E-Mail.'}
        popup = self.open_form()
        self.fill(popup)
        expect(self.page.locator('#geburtsort')).to_have_value('Bonn')
        expect(self.page.locator('#Bemerkung')).to_have_value('Kontakt per E-Mail.')

    def test_application_controls_in_an_iframe_still_require_review(self):
        popup = self.open_form()
        self.page.locator('#iframe_similarJobs').evaluate("frame => frame.srcdoc = '<label>Email<input type=email></label>'")
        expect(self.page.frame_locator('#iframe_similarJobs').locator('input')).to_be_visible()
        self.fill(popup)
        self.assertTrue(any(field['id'] == 'frame' and field['status'] == 'unsupported' for field in self.report()['fields']))

    def test_native_birth_date_keeps_iso_format(self):
        self.applicant['birth_date'] = '1992-02-29'
        popup = self.open_form()
        self.page.locator('#geburtsdatum').evaluate("input => input.type = 'date'")
        self.fill(popup)
        expect(self.page.locator('#geburtsdatum')).to_have_value('1992-02-29')

    def test_valid_additional_native_labels_are_not_discarded_by_group_recovery(self):
        popup = self.open_form()
        self.page.locator('form').evaluate("form => { const label = document.createElement('label'); label.htmlFor = 'vorname'; label.textContent = 'Nachname'; form.append(label); }")
        self.fill(popup)
        expect(self.page.locator('#vorname')).to_have_value('')
