"""Personalization, fit handoff and actual LaTeX downloads on an isolated preview.

Set APPLICATION_TEST_WEB_URL / APPLICATION_TEST_API_URL. ChatGPT is simulated.
"""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import zlib

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')
latex_template = json.loads((Path(__file__).parents[1] / 'hiring_scraper/app/latex_template.json').read_text())
evidence = 'Academic project: automated quality checks, saving three hours weekly.'
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'],
                                 executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
    context = browser.new_context(viewport={'width': 1440, 'height': 1000},
                                  permissions=['clipboard-read', 'clipboard-write'])
    context.route('https://chatgpt.com/**', lambda route: route.fulfill(body='<h1>Simulated ChatGPT</h1>'))
    profile = context.request.post(f'{api}/api/v1/profiles', data={'name': 'Personalization LaTeX trial'}).json()
    pid = profile['id']
    try:
        page = context.new_page()
        page.goto(f'{web}/profile/{pid}/edit')
        fields = {'Career evidence bank': evidence, 'Writing samples': 'I make complex work easier to follow.',
                  'Wording to avoid': 'perfect fit', 'Career goals': 'Research coordination',
                  'Working preferences and dealbreakers': 'Small teams; no regular travel.'}
        for label, value in fields.items():
            page.get_by_role('textbox', name=label).fill(value)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('saved')
        page.reload()
        for label, value in fields.items():
            expect(page.get_by_role('textbox', name=label)).to_have_value(value)
        original_tex = '\\documentclass{moderncv}\n\\name{José}{Müller}\n\\begin{document}\nResearch in Genève 2021-2024.\n\\end{document}\n'
        upload = page.get_by_label('Attach CV', exact=True)
        assert '.tex' in upload.get_attribute('accept').split(',')
        upload.set_input_files({'name': 'Original CV.tex', 'mimeType': 'application/x-tex', 'buffer': original_tex.encode()})
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_text('Original CV.tex', exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_text('Original CV.tex', exact=True)).to_be_visible()
        with page.expect_download() as downloaded:
            page.get_by_role('link', name='Download CV', exact=True).click()
        assert downloaded.value.suggested_filename.endswith('.tex')
        assert Path(downloaded.value.path()).read_bytes() == original_tex.encode()
        # Read the persisted revision as the application does; SQLite normalizes
        # timezone formatting when loading a CV in a fresh request.
        revision = next(item for item in context.request.get(f'{api}/api/v1/profiles').json()['items']
                        if item['id'] == pid)['cv']['updated_at']
        job = context.request.get(f'{api}/api/v1/jobs?limit=1').json()['items'][0]
        apply_url = f"{web}/jobs/{job['id']}/apply?profile_id={pid}"
        page.goto(apply_url)
        page.get_by_label('Brief type', exact=True).select_option('fit')
        with page.expect_popup() as opened:
            page.get_by_role('button', name='Assess fit in ChatGPT').click()
        opened.value.close()
        expect(page.get_by_role('heading', name='Is this role worth applying for?')).to_be_visible()
        clipboard = page.evaluate('navigator.clipboard.readText()')
        payload = json.loads(clipboard.split('SOURCE PAYLOAD (quoted data):\n')[-1])
        assert payload['supporting_profile']['application_personalization']['career_evidence'] == evidence
        assert 'radius-application-v1' not in clipboard
        page.get_by_label('Brief type', exact=True).select_option('application')
        with page.expect_popup() as opened:
            page.get_by_role('button', name='Copy brief & open ChatGPT').click()
        opened.value.close()
        expect(page.get_by_role('heading', name='Finish in ChatGPT.')).to_be_visible()
        clipboard = page.evaluate('navigator.clipboard.readText()')
        assert evidence in clipboard and 'radius-application-v1' in clipboard
        assert json.loads(clipboard.split('SOURCE PAYLOAD (quoted data):\n')[-1])['original_cv_text'] == original_tex.strip()
        pack = {'format': 'radius-application-v1', 'job_id': job['id'], 'profile_id': pid,
                'cv_updated_at': revision, 'cv': '# José Müller\n## Experience\n- R&D in Genève 2021-2024: 20% faster',
                'cover_letter': 'Dear hiring team,\nI coordinated research in Genève.\nJosé Müller',
                'application_answers': 'No additional application answers needed.', 'review_notes': 'Personalized for this role.'}
        canvas_sources = {
            'cv': latex_template['preamble'] + '\\section*{José Müller}\nCanvas CV from Genève 2021-2024.\\par\n' + latex_template['ending'],
            'cover_letter': latex_template['preamble'] + 'Dear hiring team,\\par\nCanvas letter from Genève.\\par\nJosé Müller\\par\n' + latex_template['ending'],
        }
        pack['latex_documents'] = canvas_sources
        token = base64.urlsafe_b64encode(zlib.compress(json.dumps(pack).encode(), 9)).decode().rstrip('=')
        page.goto(apply_url + '#applicationz=' + token)
        expect(page.get_by_role('heading', name='Your documents are ready.')).to_be_visible()
        expect(page.get_by_label('PDF layout', exact=True)).to_have_value('latex')
        page.reload()
        expect(page.get_by_role('heading', name='Your documents are ready.')).to_be_visible()
        expect(page.get_by_label('PDF layout', exact=True)).to_have_value('latex')
        with page.expect_download() as download:
            page.get_by_role('button', name='Download LaTeX source', exact=True).click()
        assert download.value.suggested_filename.endswith('-CV.tex')
        assert Path(download.value.path()).read_text() == canvas_sources['cv']
        for document in ['CV', 'Cover letter']:
            if document != 'CV':
                page.get_by_role('button', name=document, exact=True).click()
            with page.expect_download(timeout=45000) as download:
                page.get_by_role('button', name='Download PDF', exact=True).click()
            assert download.value.suggested_filename.endswith('.pdf')
            reader = PdfReader(BytesIO(Path(download.value.path()).read_bytes()))
            text = '\n'.join(p.extract_text() for p in reader.pages)
            assert 'José Müller' in text and 'Genève' in text
            assert ('Canvas CV' if document == 'CV' else 'Canvas letter') in text
        # Editing prose removes the stale snapshot for that document only.
        page.get_by_text('Edit document text', exact=True).click()
        page.get_by_role('textbox', name='Cover letter text', exact=True).fill('Dear team,\nUpdated letter from Genève.\nJosé Müller')
        with page.expect_download() as download:
            page.get_by_role('button', name='Download LaTeX source', exact=True).click()
        updated_source = Path(download.value.path()).read_text()
        assert 'Updated letter' in updated_source and 'Canvas letter' not in updated_source
        page.get_by_role('button', name='CV', exact=True).click()
        with page.expect_download() as download:
            page.get_by_role('button', name='Download LaTeX source', exact=True).click()
        assert Path(download.value.path()).read_text() == canvas_sources['cv']
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile horizontal overflow'
        # The browser-local CV path uses the same profile context and renderer.
        assert context.request.delete(f'{api}/api/v1/profiles/{pid}/cv').ok
        page.goto(apply_url)
        local_upload = page.locator('input[type=file]')
        expect(local_upload).to_have_count(1)
        assert '.tex' in local_upload.get_attribute('accept').split(',')
        local_upload.set_input_files({'name': 'local-cv.tex', 'mimeType': 'application/x-tex', 'buffer': original_tex.encode()})
        expect(page.get_by_role('textbox', name='Original CV text', exact=True)).to_have_value(original_tex)
        page.get_by_role('button', name='Save CV', exact=True).click()
        prompt_box = page.get_by_role('textbox', name='Prepared application prompt', exact=True)
        expect(prompt_box).to_have_value(re.compile(re.escape(evidence)))
        page.get_by_label('Brief type', exact=True).select_option('fit')
        assert 'radius-application-v1' not in prompt_box.input_value()
        page.get_by_label('Brief type', exact=True).select_option('application')
        local_revision = page.evaluate('(pid) => JSON.parse(localStorage.getItem(`radius:application-cv:v1:${pid}`)).updated_at', pid)
        page.get_by_role('textbox', name='Returned application package', exact=True).fill(json.dumps({**pack, 'cv_updated_at': local_revision}))
        page.get_by_role('button', name='Validate and save package', exact=True).click()
        for name, suffix in [('Download CV LaTeX source', '.tex'), ('Download letter LaTeX PDF', '.pdf')]:
            with page.expect_download(timeout=45000) as download:
                page.get_by_role('button', name=name, exact=True).click()
            assert download.value.suggested_filename.endswith(suffix)
            if suffix == '.tex':
                assert Path(download.value.path()).read_text() == canvas_sources['cv']
            else:
                assert 'Canvas letter' in '\n'.join(p.extract_text() for p in PdfReader(download.value.path()).pages)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Local mobile overflow'
        print('Profile edit/reload, saved/local fit + application briefs, real LaTeX source/CV/letter downloads, and mobile layout passed')
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
