"""Real extension UI round trip for marked base CVs and derived history."""
if __package__:
    from .test_extension import ExtensionHarness
else:
    from test_extension import ExtensionHarness
from playwright.sync_api import expect


def tex_source(title='Engineer'):
    return r'''\documentclass{article}
\begin{document}
% radius:entry {"id":"work-1","kind":"work","company":"Labs","title":"TITLE","location":"Bonn","start":"2021","end":null,"current":true}
\subsection*{TITLE}
% radius:responsibilities
\begin{itemize}
\item Built research tools.
\end{itemize}
% radius:end
% radius:entry {"id":"study-1","kind":"study","company":"University","title":"MSc","location":"Bonn","start":"2018","end":"2020","current":false}
\subsection*{MSc}
% radius:responsibilities
Studied computer science.\par
% radius:end
\end{document}'''.replace('TITLE', title)


def tex_work_history(month_precision=False):
    source = r'''\documentclass{article}
\begin{document}
% radius:entry {"id":"role-a","kind":"work","company":"North Lab","title":"Project Manager","location":"Berlin","start":"2020","end":"2024","current":false}
% radius:responsibilities
Coordinated international projects.
% radius:end
% radius:entry {"id":"role-b","kind":"work","company":"South Works","title":"Product Manager","location":"Heidelberg","start":"2022","end":"2025","current":false}
% radius:responsibilities
Translated stakeholder needs into product plans.
% radius:end
\end{document}'''
    if month_precision:
        source = source.replace('"2020"', '"2020-01"').replace('"2024"', '"2024-12"')
        source = source.replace('"2022"', '"2022-01"').replace('"2025"', '"2023-12"')
    return source


class TexExtensionTests(ExtensionHarness):
    def save_marked_profile(self, source):
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        self.load_editor_example(options)
        options.locator('#cv_text').fill(source)
        options.get_by_role('button', name='Save profile', exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        saved = options.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual([entry['company'] for entry in saved['experiences']], ['North Lab', 'South Works'], f"{options.locator('#cv_text').input_value()}: {saved}")
        options.close()

    def test_fills_an_initially_empty_smartrecruiters_experience_section(self):
        self.save_marked_profile(tex_work_history())
        url = 'https://jobs.smartrecruiters.com/oneclick-ui/company/example/publication/role'
        markup = '''<!doctype html><html><body>
          <div role="dialog" hidden aria-label="Old experience editor"></div><form>
          <label>First name<input id="first_name"></label>
          <label>Last name<input id="last_name"></label>
          <label>Email<input id="email" type="email"></label>
          <fieldset id="experience-section"><legend>Experience</legend>
            <button id="add-experience" type="button">Add experience</button>
          </fieldset><button type="submit">Apply</button></form>
          <script>
            let row = 0;
            document.querySelector('#add-experience').addEventListener('click', () => {
              const index = ++row;
              const fields = `
                <label>Company<input name="company-${index}"></label>
                <label>Job title<input name="title-${index}"></label>
                <label>Location<input name="location-${index}"></label>
                <label>Start date<input name="start-${index}"></label>
                <label>End date<input name="end-${index}"></label>
                <label>Responsibilities<textarea name="description-${index}"></textarea></label>`;
              const section = document.querySelector('#experience-section');
              if (index === 1) {
                document.querySelector('#add-experience').insertAdjacentHTML('beforebegin', fields);
                return;
              }
              const existing = [...section.querySelectorAll(':scope > label')];
              const first = document.createElement('section');
              first.setAttribute('aria-label', 'Experience 1');
              existing.forEach(field => first.append(field));
              document.querySelector('#add-experience').before(first);
              document.querySelector('#add-experience').insertAdjacentHTML('beforebegin', `<section aria-label="Experience ${index}">${fields}</section>`);
            });
            window.submissions = 0;
            document.querySelector('form').addEventListener('submit', event => {
              event.preventDefault(); window.submissions++;
            });
          </script></body></html>'''
        self.context.route(url, lambda route: route.fulfill(content_type='text/html', body=markup))
        self.page.goto(url)
        popup = self.popup()
        self.fill(popup)
        expect(self.page.locator('[aria-label="Experience 1"] input[name="company-1"]')).to_have_value('North Lab')
        expect(self.page.locator('[aria-label="Experience 1"] input[name="title-1"]')).to_have_value('Project Manager')
        expect(self.page.locator('[aria-label="Experience 2"] input[name="company-2"]')).to_have_value('South Works')
        expect(self.page.locator('[aria-label="Experience 2"] textarea[name="description-2"]')).to_have_value('Translated stakeholder needs into product plans.')
        self.assertEqual(self.report()['experiences_added'], 2)
        self.assertEqual(self.page.evaluate('window.submissions'), 0)
        self.fill(popup)
        self.assertEqual(self.report()['experiences_added'], 0)
        self.assertEqual(self.page.locator('#experience-section section').count(), 2)
        popup.close()

    def open_finest_jobs_experience_form(self, source):
        self.save_marked_profile(source)
        url = 'https://www.finest-jobs.com/bewerbung/produktmanager-d-software-solutions-1329162/?cp=BA'
        markup = '''<!doctype html><html lang="de"><body><form id="externalAppForm">
          <label for="first_name">Vorname</label><input id="first_name" name="first_name">
          <label for="last_name">Nachname</label><input id="last_name" name="last_name">
          <label for="email">E-Mail-Adresse</label><input id="email" type="email" name="email">
          <fieldset><legend>Schule, Ausbildung, Beruf</legend>
            <label for="job_experience">Berufserfahrung</label>
            <select id="job_experience" name="job_experience">
              <option value="">Bitte wählen</option><option value="0">Einsteiger</option>
              <option value="1">bis 1 Jahr</option><option value="2">bis 2 Jahre</option>
              <option value="5">bis 5 Jahre</option><option value="10">bis 10 Jahre</option>
              <option value="15">bis 15 Jahre oder mehr</option>
            </select>
          </fieldset><button type="submit">Bewerbung absenden</button>
          <script>window.submissions = 0; document.querySelector('form').addEventListener('submit', event => { event.preventDefault(); window.submissions++; });</script>
        </form></body></html>'''
        self.context.route(url, lambda route: route.fulfill(content_type='text/html', body=markup))
        self.page.goto(url)
        popup = self.popup()
        self.fill(popup)
        return popup, self.report()

    def test_finest_jobs_experience_band_uses_merged_cv_date_ranges(self):
        popup, report = self.open_finest_jobs_experience_form(tex_work_history(month_precision=True))
        self.assertEqual(self.page.locator('#job_experience').input_value(), '5', str(report))
        self.assertEqual(self.page.evaluate('window.submissions'), 0)
        experience = next(field for field in report['fields'] if field['id'] == 'job_experience')
        self.assertEqual(experience['status'], 'filled')
        self.assertIn('CV', experience['source'])
        self.fill(popup)
        expect(self.page.locator('#job_experience')).to_have_value('5')
        self.assertEqual(self.report()['filled'], 0)
        popup.close()

    def test_finest_jobs_leaves_a_year_only_duration_spanning_bands_unanswered(self):
        popup, report = self.open_finest_jobs_experience_form(tex_work_history())
        self.assertEqual(self.page.locator('#job_experience').input_value(), '', str(report))
        experience = next(field for field in report['fields'] if field['id'] == 'job_experience')
        self.assertNotEqual(experience['status'], 'filled')
        self.assertIn('approximate', experience['reason'].lower())
        self.assertEqual(self.page.evaluate('window.submissions'), 0)
        popup.close()

    def test_saves_both_sources_derives_history_and_fills_responsibility(self):
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#first_name').fill('Alex')
        options.locator('#last_name').fill('Example')
        options.locator('#email').fill('alex@example.invalid')
        options.locator('#cv_text').fill(tex_source())
        options.get_by_role('tab', name='Deutsch', exact=True).click()
        options.locator('#cv-de').fill(tex_source('Entwickler'))
        options.get_by_role('button', name='Save profile', exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        options.reload()
        expect(options.locator('#cv-de')).to_have_value(tex_source('Entwickler'))
        expect(options.get_by_role('button', name='Add experience', exact=True)).to_have_count(0)
        saved = self.worker.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(saved['experiences'][0]['description'], 'Built research tools.')
        self.assertEqual(saved['education'][0]['id'], 'study-1')
        url = 'http://127.0.0.1:43199/marked'
        for language, title in [('en', 'Engineer'), ('de', 'Entwickler')]:
            markup = f'<html lang="{language}"><form><label>Email<input type=email></label><fieldset><legend>Work experience</legend><label>Employer<input id=company></label><label>Job title<input id=title></label><label>Responsibilities<textarea id=description></textarea></label><label>Start date<input id=start></label><label><input type=checkbox>I currently work here</label></fieldset></form></html>'
            self.context.route(url, lambda route: route.fulfill(content_type='text/html', body=markup))
            self.page.goto(url)
            popup = self.popup()
            self.fill(popup)
            expect(self.page.locator('#title')).to_have_value(title)
            expect(self.page.locator('#company')).to_have_value('Labs')
            expect(self.page.locator('#description')).to_have_value('Built research tools.')
            popup.close()

    def test_language_tabs_preserve_narratives_and_share_contact_details(self):
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#email').fill('shared@example.invalid')
        options.locator('#authorization_country').fill('Germany')
        options.locator('#localized-summary').fill('English summary')
        options.locator('#localized-career_goals').fill('Build research tools.')
        options.get_by_role('tab', name='Deutsch', exact=True).click()
        expect(options.locator('#localized-summary')).to_have_value('')
        expect(options.locator('#authorization_country')).to_have_value('Germany')
        options.locator('#localized-summary').fill('Deutsche Zusammenfassung')
        options.locator('#localized-career_goals').fill('Forschungswerkzeuge entwickeln.')
        options.locator('#notice_period').fill('Drei Monate')
        options.get_by_role('button', name='Save profile', exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        options.reload()
        expect(options.locator('#localized-summary')).to_have_value('English summary')
        options.get_by_role('tab', name='Deutsch', exact=True).click()
        expect(options.locator('#localized-summary')).to_have_value('Deutsche Zusammenfassung')
        expect(options.locator('#localized-career_goals')).to_have_value('Forschungswerkzeuge entwickeln.')
        expect(options.locator('#notice_period')).to_have_value('Drei Monate')
        expect(options.locator('#email')).to_have_value('shared@example.invalid')
        expect(options.locator('#authorization_country')).to_have_value('Germany')
        saved = self.worker.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(saved['localized_content']['en']['summary'], 'English summary')
        self.assertEqual(saved['localized_content']['de']['summary'], 'Deutsche Zusammenfassung')
        self.assertEqual(saved['preferences']['authorization_country'], 'Germany')

    def test_contact_edit_preserves_existing_history_during_migration(self):
        self.save_example()
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#first_name').fill('Edited')
        options.get_by_role('button', name='Save profile', exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        saved = self.worker.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(len(saved['experiences']), len(self.applicant['experiences']))
