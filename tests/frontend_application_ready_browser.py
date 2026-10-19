"""A realistic two-page CV returns directly to working PDF/DOCX downloads."""
import base64
import zlib
import json
import os
import re
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from playwright.sync_api import sync_playwright, expect
from application_browser_helpers import application_action
from pypdf import PdfReader
from cv_realistic_fixture import realistic_docx, tailored_changes

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 390, 'height': 900}, is_mobile=True, has_touch=True, device_scale_factor=2)
    context.add_init_script("Object.defineProperty(navigator, 'pdfViewerEnabled', {value:false})")
    profile = context.request.post(f'{api}/api/v1/profiles', data={'name': 'Two-page CV test'}).json()
    pid = profile['id']
    original = realistic_docx()
    try:
        assert context.request.put(f'{api}/api/v1/profiles/{pid}/cv?filename=Mara%20Neumann%20CV.docx', data=original, headers={'Content-Type': 'application/octet-stream'}).ok
        cv = next(p for p in context.request.get(f'{api}/api/v1/profiles').json()['items'] if p['id'] == pid)['cv']
        template = context.request.get(f'{api}/api/v1/profiles/{pid}/cv/template').json()
        job = context.request.get(f'{api}/api/v1/jobs?query=projekt&limit=1').json()['items'][0]
        package = {'format': 'radius-application-v1', 'job_id': job['id'], 'profile_id': pid, 'cv_updated_at': cv['updated_at'],
                   'cv': tailored_changes(template['paragraphs']),
                   'cover_letter': 'Dear hiring team,\nMy experience managing research projects, consortium proposals and funding reports aligns closely with your role. At Rheinbogen Systems, I coordinate six research and digitalization projects with a combined €2.4 million budget. I would bring structured planning, dependable financial reporting and clear partner communication to your team.\nKind regards,\nMara Neumann',
                   'application_answers': 'Languages: German native; English C1; French B1.\nLocation: Karlsruhe.',
                   'review_notes': 'Emphasized research funding, consortium coordination and financial reporting.'}
        page = context.new_page()
        page.goto(f"{web}/jobs/{job['id']}/apply?profile_id={pid}#applicationz=" + base64.urlsafe_b64encode(zlib.compress(json.dumps(package, ensure_ascii=False).encode(), 9)).decode().rstrip('='))
        expect(page.get_by_role('heading', name='Your documents are ready.', exact=True)).to_be_visible(timeout=25000)
        expect(page.get_by_role('button', name='Download PDF', exact=True)).to_be_enabled()
        expect(page.get_by_role('textbox', name='CV paragraph 1', exact=True)).to_have_count(0)
        assert 'CONFIRM' not in page.locator('main').inner_text()
        assert not page.get_by_role('button', name='Confirm', exact=False).count()
        # The app must draw real pages without depending on a native PDF plugin.
        canvas = page.get_by_role('img', name='CV page 1 of 2', exact=True)
        expect(canvas).to_be_visible(timeout=45000)
        assert canvas.evaluate('el => el.width > 200 && el.height > 300')
        def inked(el):
            return el.evaluate('''canvas => {
                const pixels = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
                let ink = 0;
                for(let i=0;i<pixels.length;i+=4) if(pixels[i+3]>0 && Math.min(pixels[i],pixels[i+1],pixels[i+2])<180) ink++;
                return ink;
            }''')
        expect(canvas).to_have_attribute('aria-description', re.compile('Mara Neumann'))
        assert inked(canvas) > 1000, 'Preview must contain document ink, not an empty canvas'
        page.get_by_role('button', name='Next CV page', exact=True).click()
        second_page = page.get_by_role('img', name='CV page 2 of 2', exact=True)
        expect(second_page).to_be_visible()
        assert inked(second_page) > 1000, 'The second CV page must render too'
        page.get_by_role('button', name='Previous CV page', exact=True).click()
        expect(canvas).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role('button', name='Download PDF', exact=True).click()
        assert job['company_name'] in download.value.suggested_filename
        assert download.value.suggested_filename.endswith('-CV.pdf')
        exported = PdfReader(BytesIO(Path(download.value.path()).read_bytes()))
        assert len(exported.pages) == 2
        text = '\n'.join(p.extract_text() for p in exported.pages)
        assert 'Mara Neumann' in text and 'Research & Funding Programs' in text
        assert 'Community involvement' in exported.pages[1].extract_text()
        assert not any(marker in text for marker in ['[CONFIRM', '[TODO', '[INSERT'])
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='Download editable DOCX', exact=True).click()
        assert job['company_name'] in downloaded.value.suggested_filename
        assert downloaded.value.suggested_filename.endswith('-CV.docx')
        with ZipFile(downloaded.value.path()) as changed, ZipFile(BytesIO(original)) as source:
            for name in source.namelist():
                if name != 'word/document.xml':
                    assert changed.read(name) == source.read(name), name
        assert context.request.get(f'{api}/api/v1/profiles/{pid}/cv').body() == original
        # An unfinished model reply cannot replace a finished application.
        application_action(page, 'Paste a new response')
        bad = {**package, 'cover_letter': 'Dear team,\n[CONFIRM: relevant achievement]'}
        page.get_by_role('textbox', name='Application package', exact=True).fill(json.dumps(bad))
        page.get_by_role('button', name='Open documents', exact=True).click()
        expect(page.get_by_role('alert').filter(has_text='unfinished')).to_be_visible()
        page.get_by_role('button', name='Keep current documents', exact=True).click()
        expect(page.get_by_role('heading', name='Your documents are ready.', exact=True)).to_be_visible()
        # Editing remains optional and can be reached after downloading.
        page.get_by_text('Edit document text', exact=True).click()
        expect(page.get_by_role('textbox', name='CV paragraph 1', exact=True)).to_have_value('Mara Neumann')
        page.get_by_role('textbox', name='CV paragraph 1', exact=True).fill('[TODO: name]')
        page.get_by_role('button', name='Download PDF', exact=True).click()
        expect(page.get_by_role('alert').filter(has_text='unfinished')).to_be_visible()
        page.get_by_role('textbox', name='CV paragraph 1', exact=True).fill('Mara Neumann Updated')
        expect(canvas).to_have_attribute('aria-description', re.compile('Mara Neumann Updated'), timeout=45000)
        for theme in ['light', 'dark']:
            page.evaluate('(theme) => { document.documentElement.dataset.theme = theme }', theme)
            for width in [320, 390, 768, 1440]:
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (theme, width)
        print('Two-page CV returns to direct PDF/DOCX downloads without a confirmation step; source and package formatting remain intact')
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
