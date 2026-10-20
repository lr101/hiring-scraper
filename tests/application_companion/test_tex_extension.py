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


class TexExtensionTests(ExtensionHarness):
    def test_saves_both_sources_derives_history_and_fills_responsibility(self):
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#first_name').fill('Alex')
        options.locator('#last_name').fill('Example')
        options.locator('#email').fill('alex@example.invalid')
        options.locator('#cv_text').fill(tex_source())
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

    def test_contact_edit_preserves_existing_history_during_migration(self):
        self.save_example()
        options = self.context.new_page()
        options.goto(f'chrome-extension://{self.extension_id}/options.html')
        options.locator('#first_name').fill('Edited')
        options.get_by_role('button', name='Save profile', exact=True).click()
        expect(options.locator('#status')).to_have_text('Profile saved in this browser.')
        saved = self.worker.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(len(saved['experiences']), len(self.applicant['experiences']))
