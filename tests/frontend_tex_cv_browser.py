"""English/German marked base CV upload, language selection and structured review."""
import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    context = browser.new_context()
    profile = context.request.post(f'{api}/api/v1/profiles', data={'name': 'Marked LaTeX CV browser test'}).json()
    pid = profile['id']
    try:
        jobs = context.request.get(f'{api}/api/v1/companion/jobs?limit=1').json()['items']
        jid = jobs[0]['id']
        job = context.request.get(f'{api}/api/v1/jobs/{jid}').json()
        job['description'] = 'Please apply in German. We are looking for an engineer.'
        page = context.new_page()
        page.route(f'{api}/api/v1/jobs/{jid}', lambda route: route.fulfill(json=job))
        page.goto(f'{web}/profile/{pid}/edit')
        source = (Path(__file__).parent / 'fixtures/documents/flowing_en_cv.tex').read_text()
        en = source.replace('Operations Analyst | SQL', 'English operations analyst | SQL')
        de = source.replace('Operations Analyst | SQL', 'Deutsche Betriebsanalyse | SQL')
        page.get_by_label('English base CV', exact=True).set_input_files({'name': 'cv-en.tex', 'mimeType': 'application/x-tex', 'buffer': en.encode()})
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        page.get_by_label('German base CV', exact=True).set_input_files({'name': 'cv-de.tex', 'mimeType': 'application/x-tex', 'buffer': de.encode()})
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_text('Profile saved. Its search area is available on the jobs page.', exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_role('link', name='Download cv-en.tex', exact=True)).to_be_visible()
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        expect(page.get_by_role('link', name='Download cv-de.tex', exact=True)).to_be_visible()
        expect(page.get_by_text('Application work history', exact=True)).to_have_count(0)
        page.goto(f'{web}/jobs/{jid}/apply?profile_id={pid}')
        expect(page.get_by_role('combobox', name='Document language')).to_have_value('de')
        expect(page.get_by_text('Deutsche Betriebsanalyse', exact=False)).to_be_visible()
        page.get_by_role('combobox', name='Document language').select_option('en')
        expect(page.get_by_text('English operations analyst', exact=False)).to_be_visible()
        page.get_by_role('button', name='Confirm and save base CV').click()
        expect(page.get_by_text('Review or edit base CV', exact=True)).to_be_visible()
        structure = context.request.get(f'{api}/api/v1/profiles/{pid}/cv/structure?language=en').json()
        assert structure['source'] == 'stored'
        assert structure['document']['headline']['text'].startswith('English operations analyst')
        print('Both language sources persisted and the structured CV was confirmed.')
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
