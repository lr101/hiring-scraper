"""Offline browser regressions derived from company-form gaps, plus safety variants."""
from copy import deepcopy
import os
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright
from experiments.autofill_corpus import PROFILE, JOBS
from experiments.evaluate_real_autofill import GUARDS

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('RADIUS_EXTENSION_BROWSER_TESTS') == '1', 'browser checks opt in')
class RealFormAutofillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, args=['--proxy-server=http://127.0.0.1:9'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(offline=True, service_workers='block')
        self.requests = []
        def deny(route):
            self.requests.append(route.request.url)
            route.abort()
        self.context.route('**/*', deny)
        self.page = self.context.new_page()
        self.profile = {**deepcopy(PROFILE), 'experiences': deepcopy(JOBS[:2])}

    def tearDown(self):
        self.assertEqual(self.requests, [])
        self.context.close()

    def fill(self, body):
        self.page.set_content('<form><label>Email<input type=email></label><label>Portfolio<input></label>' + body + '</form>')
        self.page.evaluate(GUARDS)
        for name in ('history.js', 'best-effort.js'):
            self.page.add_script_tag(path=str(ROOT / 'extensions/application-companion' / name))
        self.page.evaluate('async p => {await applicationHistory.prepare(p);await applicationFields.run(p)}', self.profile)

    def values(self, expected):
        for selector, value in expected.items():
            self.assertEqual(self.page.locator(selector).input_value(), value, selector)
        before = self.page.locator('input,textarea').evaluate_all('(els)=>els.map(e=>e.value)')
        self.page.evaluate('async p=>{await applicationHistory.prepare(p);await applicationFields.run(p)}', self.profile)
        self.assertEqual(self.page.locator('input,textarea').evaluate_all('(els)=>els.map(e=>e.value)'), before)
        self.assertEqual(self.page.evaluate('window.audit.submitAttempts'), 0)
        self.assertEqual(self.page.evaluate('window.audit.transportAttempts'), [])

    def test_required_prefixes_do_not_hide_contact_labels(self):
        self.fill('<label>Pflichtfeld Vorname*<input id=first></label><label>Pflichtfeld Nachname*<input id=last></label><label>Pflichtfeld Straße / Hausnummer*<input id=street></label>')
        self.values({'#first': 'Mara', '#last': 'Neumann', '#street': 'Example Street 4'})

    def test_bare_name_next_to_given_name_is_surname(self):
        self.fill('<p>Name*<span><input name=Name id=last></span></p><p>Vorname*<span><input name=Vorname id=first></span></p>')
        self.values({'#last': 'Neumann', '#first': 'Mara'})

    def test_bare_name_without_given_name_remains_complete_name(self):
        self.fill('<label>Name<input id=name></label>')
        self.values({'#name': 'Mara Neumann'})

    def test_name_pair_in_separate_question_fieldsets_uses_contact_section(self):
        self.fill('<section aria-label="Contact details"><fieldset><legend>Name</legend><label>Name<input id=answer></label></fieldset><fieldset><legend>Vorname</legend><label>Vorname<input id=given></label></fieldset></section>')
        self.values({'#answer': 'Neumann', '#given': 'Mara'})

    def test_name_pair_searches_beyond_inner_group_with_unrelated_controls(self):
        self.fill('<section aria-label="Contact details"><fieldset><label>Name<input id=answer></label><label>Salutation<input></label></fieldset><fieldset><label>Vorname<input id=given></label></fieldset></section>')
        self.values({'#answer': 'Neumann', '#given': 'Mara'})

    def test_explicit_full_name_is_not_reclassified_by_given_name(self):
        self.fill('<label>Full name<input id=full></label><label>First name<input id=first></label>')
        self.values({'#full': 'Mara Neumann', '#first': 'Mara'})

    def test_full_postal_address_and_postal_city_are_composed_from_known_facts(self):
        self.fill('<label>Adresse<input id=address placeholder="Musterstraße, 5 92318 Neumarkt"></label><label>PLZ / Ort<input id=place></label>')
        self.values({'#address': 'Example Street 4, 76131 Karlsruhe', '#place': '76131 Karlsruhe'})

    def test_split_street_does_not_receive_combined_address(self):
        self.fill('<label>Straße<input id=street></label><label>Hausnummer<input id=number></label>')
        self.values({'#street': '', '#number': ''})

    def test_split_address_in_separate_question_fieldsets_is_preserved(self):
        self.fill('<section aria-label="Postal address"><fieldset><legend>Street</legend><label>Street<input id=street></label></fieldset><fieldset><legend>House number</legend><label>House number<input id=number></label></fieldset></section>')
        self.values({'#street': '', '#number': ''})

    def test_split_address_searches_beyond_inner_group_with_city(self):
        self.fill('<section aria-label="Postal address"><fieldset><label>Street<input id=street></label><label>City<input id=town></label></fieldset><fieldset><label>House number<input id=number></label></fieldset></section>')
        self.values({'#street': '', '#number': '', '#town': 'Karlsruhe'})

    def test_related_contact_questions_ignore_foreign_person_controls(self):
        self.fill('<fieldset><label>Name<input id=answer></label><label>Street<input id=street></label></fieldset><fieldset><legend>Reference</legend><label>Vorname<input id=given></label><label>House number<input id=number></label></fieldset>')
        self.values({'#answer': 'Mara Neumann', '#street': 'Example Street 4', '#given': '', '#number': ''})

    def test_history_questions_do_not_reclassify_applicant_name(self):
        for heading in ('Work experience', 'Previous employment'):
            with self.subTest(heading=heading):
                self.fill(f'<label>Name<input id=answer></label><section aria-label="{heading}"><label>Vorname<input id=given></label></section>')
                self.values({'#answer': 'Mara Neumann', '#given': ''})

    def test_incomplete_postal_address_is_left_for_review(self):
        self.profile['postal_code'] = ''
        self.fill('<label>Full postal address<input id=address></label>')
        self.values({'#address': ''})

    def test_numbered_history_entries_copy_separate_saved_records(self):
        self.fill('<label>Beruflicher Werdegang 1:<input id=one></label><label>Beruflicher Werdegang 2:<input id=two></label>')
        self.values({'#one': '2021-03 – Present: North Lab | Engineer | Berlin. Built reporting tools.', '#two': '2018 – 2020: South Works | Analyst | Munich. Analyzed delivery data.'})

    def test_full_history_textarea_copies_all_saved_records(self):
        self.fill('<label>Beruflicher Werdegang<textarea id=history></textarea></label>')
        self.values({'#history': '2021-03 – Present: North Lab | Engineer | Berlin. Built reporting tools.\n2018 – 2020: South Works | Analyst | Munich. Analyzed delivery data.'})

    def test_combined_employer_duties_and_period_rows_use_distinct_jobs(self):
        self.fill('<fieldset><legend>Beruflicher Werdegang</legend><label>von - bis<input id=p1></label><label>Arbeitgeber / Tätigkeiten<textarea id=j1></textarea></label><label>von - bis<input id=p2></label><label>Arbeitgeber / Tätigkeiten<textarea id=j2></textarea></label></fieldset>')
        self.values({'#p1': '2021-03 – Present', '#j1': 'North Lab | Engineer. Built reporting tools.', '#p2': '2018 – 2020', '#j2': 'South Works | Analyst. Analyzed delivery data.'})

    def test_populated_combined_history_row_blocks_source_order_guess(self):
        self.fill('<fieldset><legend>Beruflicher Werdegang</legend><label>von - bis<input id=p1></label><label>Arbeitgeber / Tätigkeiten<textarea id=j1>Other employer</textarea></label><label>von - bis<input id=p2></label><label>Arbeitgeber / Tätigkeiten<textarea id=j2></textarea></label></fieldset>')
        self.values({'#p1': '', '#j1': 'Other employer', '#p2': '', '#j2': ''})

    def test_numbered_history_preserves_collection_when_a_draft_differs(self):
        self.fill('<label>Beruflicher Werdegang 1:<input id=one value="Other employer"></label><label>Beruflicher Werdegang 2:<input id=two></label>')
        self.values({'#one': 'Other employer', '#two': ''})

    def test_full_history_is_not_truncated_to_fit_a_short_field(self):
        self.fill('<label>Beruflicher Werdegang<textarea id=history maxlength=20></textarea></label>')
        self.values({'#history': ''})

    def test_combined_period_does_not_invent_required_days(self):
        self.fill('<fieldset><legend>Beruflicher Werdegang</legend><label>von - bis<input id=period placeholder="DD.MM.YYYY - DD.MM.YYYY"></label><label>Arbeitgeber / Tätigkeiten<textarea id=duties></textarea></label></fieldset>')
        self.values({'#period': '', '#duties': 'North Lab | Engineer. Built reporting tools.'})

    def test_combined_period_checks_numeric_pattern_before_writing(self):
        self.fill(r'<fieldset><legend>Work history</legend><label>Employment period<input id=period pattern="[0-9]{2}\.[0-9]{2}\.[0-9]{4} - [0-9]{2}\.[0-9]{2}\.[0-9]{4}"></label><label>Employer duties<textarea id=duties></textarea></label></fieldset>')
        self.values({'#period': '', '#duties': 'North Lab | Engineer. Built reporting tools.'})

    def test_latest_employer_uses_most_recent_saved_job(self):
        self.fill('<label>Letzter Arbeitgeber<input id=company></label>')
        self.values({'#company': 'North Lab'})

    def test_career_h4_with_broken_label_for_maps_facts_without_inventing_days(self):
        self.fill('<section><h4>Ihr beruflicher Werdegang</h4><div><label for=missing1>Unternehmen:</label><input id=company></div><div><label for=missing2>Tätigkeitsbezeichnung:</label><input id=role></div><div><label for=missing3>Standort:</label><input id=location></div><div><label for=missing4>Beginn:</label><input id=start type=date></div><div><label for=missing5>Ende:</label><input id=end type=date></div></section>')
        self.values({'#company': 'North Lab', '#role': 'Engineer', '#location': 'Berlin', '#start': '', '#end': ''})

    def test_plain_paragraph_name_pair_ignores_explicit_leave_empty_field(self):
        self.fill('<p>Name*<span><input name=Name id=surname></span></p><p>Vorname*<span><input name=Vorname id=given></span></p><span style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)"><label for=trap>Bitte lasse dieses Feld leer.</label><input id=trap name=first-name></span>')
        self.values({'#surname': 'Neumann', '#given': 'Mara', '#trap': ''})

    def test_plain_paragraph_history_label_copies_saved_work(self):
        self.fill('<p>Beruflicher Werdegang und Tätigkeiten (Stichpunkte):*<br><span><textarea name=Werdegang id=history></textarea></span></p>')
        self.values({'#history': '2021-03 – Present: North Lab | Engineer | Berlin. Built reporting tools.\n2018 – 2020: South Works | Analyst | Munich. Analyzed delivery data.'})

    def test_history_label_in_immediately_preceding_paragraph(self):
        self.fill('<p>Beruflicher Werdegang und Tätigkeiten (Stichpunkte):*</p><p><span><textarea name=Werdegang id=history></textarea></span></p>')
        self.values({'#history': '2021-03 – Present: North Lab | Engineer | Berlin. Built reporting tools.\n2018 – 2020: South Works | Analyst | Munich. Analyzed delivery data.'})

    def test_paragraph_does_not_borrow_label_explicitly_owned_by_another_control(self):
        self.fill('<div hidden><input id=target></div><p><label for=target>First name</label><input id=opaque></p>')
        self.values({'#opaque': '', '#target': ''})

    def test_preceding_paragraph_does_not_borrow_an_explicitly_owned_history_label(self):
        self.fill('<div hidden><textarea id=target></textarea></div><p><label for=target>Beruflicher Werdegang</label></p><p><textarea id=opaque></textarea></p>')
        self.values({'#opaque': '', '#target': ''})

    def test_reference_and_education_history_are_never_applicant_work(self):
        self.fill('<fieldset><legend>Reference</legend><label>Beruflicher Werdegang<textarea id=ref></textarea></label><label>Letzter Arbeitgeber<input id=company></label></fieldset><fieldset><legend>Education</legend><label>Beruflicher Werdegang 1<input id=school></label></fieldset>')
        self.values({'#ref': '', '#company': '', '#school': ''})
