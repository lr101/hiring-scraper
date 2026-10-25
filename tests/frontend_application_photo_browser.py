"""Real UI photo selection, ephemeral storage, exact custom source bundle and local rendering.

Usage: python tests/frontend_application_photo_browser.py WEB_URL API_URL
"""
import base64
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

from playwright.sync_api import sync_playwright, expect
from pypdf import PdfReader
from test_application_documents import ApplicationDocumentTests
from test_tex_cv import source

web, api = (value.rstrip('/') for value in sys.argv[1:3])
original = source().replace(r'\begin{document}', r'\usepackage{graphicx}' + '\n' + r'\begin{document}' + '\n' + r'\includegraphics[width=25mm]{portrait_cropped.png}' + '\nAlex Example\\par\n% radius:profile\nInnovation engineer\\par\n% radius:endprofile\nalex@example.org\\par\n')
photo = base64.b64decode(ApplicationDocumentTests.photo().split(',', 1)[1])
with sync_playwright() as pw, tempfile.TemporaryDirectory(prefix='cv-photo-browser-') as folder:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 1280, 'height': 1000})
    profile = context.request.post(api + '/api/v1/profiles', data={'name': 'CV photo render test'}).json()
    pid = profile['id']
    try:
        result = context.request.put(f'{api}/api/v1/profiles/{pid}/cv?language=en&filename=test.tex', data=original.encode(), headers={'Content-Type': 'application/octet-stream'})
        assert result.ok, result.text()
        revision = next(item['cv']['updated_at'] for item in context.request.get(api + '/api/v1/profiles').json()['items'] if item['id'] == pid)
        job = context.request.get(f'{api}/api/v1/jobs?limit=1').json()['items'][0]
        jid = job['id']
        page = context.new_page()
        page.goto(f'{web}/jobs/{jid}/apply?profile_id={pid}')
        page.get_by_text('Options', exact=True).click()
        page.get_by_role('button', name='Show brief', exact=True).click()
        prompt = page.get_by_role('textbox', name='Prepared application prompt').input_value()
        assert 'Innovation engineer' in prompt and 'alex@example.org' in prompt
        page.get_by_text('Options', exact=True).click()
        page.get_by_role('button', name='I already have a result', exact=True).click()
        pack = {'format': 'radius-application-v1', 'job_id': jid, 'profile_id': pid, 'cv_updated_at': revision,
                'cv': 'Alex Example\nInnovation project coordinator\nalex@example.org\nBuilt research tools.',
                'cover_letter': 'Alex Example\nBonn\n\nExample Ltd\n\n25 October 2026\n\nApplication for Engineer\n\nDear team,\n\nI build tools.\n\nYours sincerely,\nAlex Example',
                'application_answers': 'No questions requested.', 'review_notes': 'Description only.', 'latex_documents': {'cv': original.replace('Innovation engineer', 'Innovation project coordinator')}}
        page.get_by_role('textbox', name='Application package', exact=True).fill(json.dumps(pack))
        page.get_by_role('button', name='Open documents', exact=True).click()
        expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
        page.get_by_text('CV photo (optional)', exact=True).click()
        field = page.get_by_label('Photo for CV rendering', exact=True)
        field.set_input_files({'name': 'invalid.png', 'mimeType': 'image/png', 'buffer': b'not an image'})
        expect(page.get_by_role('alert').filter(has_text='Could not read this PNG photo.')).to_be_visible()
        field.set_input_files({'name': 'portrait.png', 'mimeType': 'image/png', 'buffer': photo})
        expect(page.get_by_alt_text('Selected CV photo', exact=True)).to_be_visible()
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='Download source + photo ZIP', exact=True).click()
        with ZipFile(downloaded.value.path()) as bundle:
            assert bundle.read('application.tex').decode() == original.replace('Innovation engineer', 'Innovation project coordinator')
            assert bundle.read('portrait_cropped.png') == photo
            bundle.extractall(folder)
        subprocess.run(['lualatex', '-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error', 'application.tex'], cwd=folder, check=True, capture_output=True, timeout=40)
        pdf = PdfReader(Path(folder) / 'application.pdf')
        text = '\n'.join(page.extract_text() for page in pdf.pages)
        assert all(value in text for value in ['Alex Example', 'Innovation project coordinator', 'alex@example.org', 'Built research tools.'])
        assert any(obj.get_object().get('/Subtype') == '/Image' for page in pdf.pages for obj in page['/Resources']['/XObject'].get_object().values())
        stored = page.evaluate('JSON.stringify({...localStorage})')
        assert 'data:image/png' not in stored
        page.screenshot(path='/tmp/cv-photo-ui.png', full_page=True)
        page.reload()
        expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
        expect(page.get_by_alt_text('Selected CV photo', exact=True)).to_have_count(0)
        assert context.request.get(f'{api}/api/v1/profiles/{pid}/cv').body() == original.encode()
        print('PASS: header retained; profile personalized; PNG upload validated; exact source/photo ZIP compiles; photo embedded; draft excludes photo; original CV unchanged.')
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
