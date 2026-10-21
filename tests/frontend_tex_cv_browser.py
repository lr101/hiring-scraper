"""English/German marked base CV upload, selection, return and actual PDF preview."""
import base64
import json
import os
from pathlib import Path
import zlib
from playwright.sync_api import expect, sync_playwright
from test_tex_cv import source

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
        en = source().replace('Built research tools.', 'English base responsibility.')
        de = source().replace('Built research tools.', 'Deutsche Basisaufgabe.')
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
        ready = page.locator('[data-original-cv-text]')
        expect(ready).to_have_attribute('data-original-cv-text', de.strip())
        page.get_by_text('Options', exact=True).click()
        page.get_by_role('button', name='Show brief', exact=True).click()
        prompt = page.get_by_role('textbox', name='Prepared application prompt')
        expect(prompt).to_contain_text('Deutsche Basisaufgabe.')
        page.get_by_text('Options', exact=True).click()
        page.get_by_role('combobox', name='Document language').select_option('English')
        expect(ready).to_have_attribute('data-original-cv-text', en.strip())
        expect(prompt).to_contain_text('English base responsibility.')
        payload = json.loads(prompt.input_value().split('SOURCE PAYLOAD (quoted data):')[-1])
        assert payload['cv_history']['experiences'][0]['description'] == 'English base responsibility.'
        page.get_by_role('button', name='I already have a result', exact=True).click()
        profiles = context.request.get(f'{api}/api/v1/profiles').json()['items']
        metadata = next(item for item in profiles if item['id'] == pid)['cv']
        pack = {'format': 'radius-application-v1', 'job_id': jid, 'profile_id': pid, 'cv_updated_at': metadata['updated_at'], 'cv': 'Alex Example\nEnglish base responsibility.', 'cover_letter': 'Dear team,\nI build research tools.', 'application_answers': 'None', 'review_notes': 'Reviewed', 'latex_documents': {'cv': en}}
        token = base64.urlsafe_b64encode(zlib.compress(json.dumps(pack).encode())).decode().rstrip('=')
        page.goto(f'{web}/jobs/{jid}/apply?profile_id={pid}#applicationz={token}')
        expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_role('button', name='Download editable DOCX', exact=True)).to_have_count(0)
        expect(page.locator('.template-pdf-preview canvas').first).to_be_visible(timeout=30000)
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='Download LaTeX source', exact=True).click()
        assert Path(downloaded.value.path()).read_text() == en
        page.screenshot(path='/tmp/tex-cv-browser.png', full_page=True)
        print('Both language sources persisted; automatic German and explicit English selection, marked return, PDF preview and exact source download passed.')
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
