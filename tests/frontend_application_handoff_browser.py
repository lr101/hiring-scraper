"""End-to-end saved-CV application flow on an isolated seeded preview.
Set APPLICATION_TEST_WEB_URL and APPLICATION_TEST_API_URL. No real ChatGPT calls.
"""
import json
import os
import re
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from playwright.sync_api import sync_playwright, expect
from application_browser_helpers import application_action
from pypdf import PdfReader
from test_cv_template import template_docx

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')
original = template_docx()
with sync_playwright() as pw:
    options = dict(headless=True, args=['--no-sandbox'])
    if os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'):
        options['executable_path'] = os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']
    browser = pw.chromium.launch(**options)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, permissions=['clipboard-read', 'clipboard-write'])
    context.route('https://chatgpt.com/**', lambda route: route.fulfill(body='<h1>ChatGPT handoff destination</h1>', content_type='text/html'))
    context.add_init_script('window.print = () => { window.printCalled = true }')
    page = context.new_page()
    page.set_default_timeout(15000)
    errors, profile_ids = [], []
    def capture_error(error):
        # Fast test navigations can interrupt Chromium's cross-document CSS
        # transition. It reports this native animation cancellation as pageerror.
        if str(error) != 'Transition was aborted because of invalid state. ViewTransition opt-in disabled':
            errors.append(str(error))
    page.on('pageerror', capture_error)
    try:
        # Attach the original once while creating a profile, then verify persistence.
        job = context.request.get(f'{api}/api/v1/jobs?query=projekt&limit=1').json()['items'][0]
        page.route(f'{api}/api/v1/profiles', lambda route: route.fulfill(json={'items': []}) if route.request.method == 'GET' else route.continue_())
        page.goto(f"{web}/jobs/{job['id']}/apply")
        page.get_by_role('link', name='Create profile', exact=True).click()
        page.unroute(f'{api}/api/v1/profiles')
        page.get_by_role('textbox', name='Profile name', exact=True).fill('Saved CV browser test')
        instructions = 'Write in German. Use a warm, direct voice. Focus on project coordination.'
        page.get_by_role('textbox', name='Custom application instructions', exact=True).fill(instructions)
        page.get_by_label('Attach CV', exact=True).set_input_files({'name': 'original-template.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': original})
        page.get_by_role('button', name='Save & continue', exact=True).click()
        expect(page).to_have_url(re.compile(r'/jobs/\d+/apply\?profile_id=\d+$'))
        profile_id = int(page.url.split('profile_id=')[1])
        profile_ids.append(profile_id)
        page.goto(f'{web}/profile/{profile_id}/edit')
        expect(page.get_by_text('original-template.docx', exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_text('original-template.docx', exact=True)).to_be_visible()
        expect(page.get_by_role('textbox', name='Custom application instructions', exact=True)).to_have_value(instructions)
        assert context.request.get(f'{api}/api/v1/profiles/{profile_id}/cv').body() == original
        job = context.request.get(f'{api}/api/v1/jobs?query=projekt&limit=1').json()['items'][0]
        page.goto(f"{web}/jobs/{job['id']}?profile_id={profile_id}")
        expect(page.locator('.application-finish-card')).to_have_count(0)
        page.get_by_role('link', name='Start application', exact=False).click()
        expect(page).to_have_url(f"{web}/jobs/{job['id']}/apply?profile_id={profile_id}")
        expect(page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        expect(page.get_by_role('navigation', name='Main navigation', exact=True)).to_have_count(0)
        expect(page.locator('.handoff-guide')).to_have_count(0)
        expect(page.get_by_role('textbox', name='Prepared application prompt', exact=True)).to_have_count(0)
        assert page.locator('input[type=file]').count() == 0, 'CV should already be attached to the profile'
        with page.expect_popup() as opened:
            page.get_by_role('button', name='Copy brief & open ChatGPT').click()
        chat = opened.value
        expect(chat.get_by_role('heading', name='ChatGPT handoff destination')).to_be_visible()
        copied = page.evaluate('navigator.clipboard.readText()')
        assert 'Alex Example' in copied and 'Coordinate projects' in copied and job['url'] in copied
        assert instructions in copied, 'Saved writing preferences must reach the actual copied ChatGPT brief'
        assert 'No separate file attachment' in copied and 'cv_template_paragraphs' in copied
        assert chat.url == 'https://chatgpt.com/'
        preferences = json.loads(re.search(r'WRITING PREFERENCES \(applicant instructions\):\n(.*?)\n\nSOURCE PAYLOAD', copied, re.S).group(1))
        assert preferences['profile_instructions'] == instructions
        assert preferences['document_language'] == {'mode': 'match_job'}
        chat.close()
        # The explicit selector and one-off preferences reach the copied brief,
        # even when the profile asks for German.
        application_action(page, 'Back to brief')
        page.get_by_text('Options', exact=True).click()
        page.get_by_role('combobox', name='Document language', exact=True).select_option('English')
        page.get_by_role('textbox', name='Additional instructions', exact=True).fill('Use a concise voice for this application.')
        with page.expect_popup() as override_chat:
            page.get_by_role('button', name='Copy brief & open ChatGPT').click()
        override = page.evaluate('navigator.clipboard.readText()')
        preferences = json.loads(re.search(r'WRITING PREFERENCES \(applicant instructions\):\n(.*?)\n\nSOURCE PAYLOAD', override, re.S).group(1))
        assert preferences == {'document_language': {'mode': 'explicit', 'language': 'English'},
                               'profile_instructions': instructions, 'application_instructions': 'Use a concise voice for this application.'}
        override_chat.value.close()
        template = context.request.get(f'{api}/api/v1/profiles/{profile_id}/cv/template').json()
        paragraph = next(row for row in template['paragraphs'] if row['text'] == 'Coordinate projects')
        reply = '<<<CV>>>\n' + json.dumps([{'id': paragraph['id'], 'text': 'Coordinate programs'}]) + '\n<<<COVER_LETTER>>>\nDear team,\nMy letter only.\n<<<APPLICATION_ANSWERS>>>\nNotice: unknown\n<<<REVIEW_NOTES>>>\nPRIVATE REVIEW: check dates.'
        expect(page.get_by_role('textbox', name='Application package', exact=True)).not_to_be_visible()
        page.get_by_text('Import manually', exact=True).click()
        page.get_by_role('textbox', name='Application package', exact=True).fill(reply)
        with page.expect_response(lambda result: '/cv/export?format=pdf&preview=true' in result.url, timeout=45000) as rendering:
            page.get_by_role('button', name='Open documents', exact=True).click()
        assert rendering.value.ok and rendering.value.headers['content-type'] == 'application/pdf'
        expect(page.get_by_title('Original template PDF preview')).to_be_visible()
        field = page.get_by_role('textbox', name=f"CV paragraph {paragraph['id'] + 1}", exact=True, include_hidden=True)
        expect(field).to_have_value('Coordinate programs')
        with page.expect_download() as download:
            page.get_by_role('button', name='Download PDF', exact=True).click()
        pdf = PdfReader(BytesIO(Path(download.value.path()).read_bytes()))
        text = '\n'.join(row.extract_text() for row in pdf.pages)
        assert 'ORIGINAL CV TEMPLATE' in text and 'Coordinate programs' in text and 'PRIVATE REVIEW' not in text
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='Download editable DOCX', exact=True).click()
        with ZipFile(BytesIO(Path(downloaded.value.path()).read_bytes())) as changed, ZipFile(BytesIO(original)) as source:
            assert changed.read('word/header1.xml') == source.read('word/header1.xml')
            assert b'Coordinate programs' in changed.read('word/document.xml')
        assert context.request.get(f'{api}/api/v1/profiles/{profile_id}/cv').body() == original
        application_action(page, 'Paste a new response')
        page.get_by_role('textbox', name='Application package', exact=True).fill(reply.replace(str(paragraph['id']), '9999', 1))
        page.get_by_role('button', name='Open documents', exact=True).click()
        expect(page.get_by_role('alert').filter(has_text='invalid')).to_be_visible()
        page.get_by_role('button', name='Keep current documents', exact=True).click()
        expect(field).to_have_value('Coordinate programs')
        page.get_by_role('group', name='Choose document').get_by_role('button', name='Cover letter', exact=True).click()
        expect(page.get_by_role('textbox', name='Cover letter text', exact=True, include_hidden=True)).to_have_value('Dear team,\nMy letter only.')
        with page.expect_popup() as printed:
            page.get_by_role('button', name='Print / save PDF', exact=True).click()
        assert 'PRIVATE REVIEW' not in printed.value.locator('body').inner_text()
        assert printed.value.evaluate('window.printCalled')
        printed.value.close()
        for theme in ['light', 'dark']:
            page.evaluate('(theme) => { document.documentElement.dataset.theme = theme }', theme)
            for width in [1440, 768, 390, 320]:
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}px in {theme}'
        # No-CV profiles get a direct attach-and-return route, not a second CV picker per job.
        other = context.request.post(f'{api}/api/v1/profiles', data={'name': 'No CV browser test'}).json()
        profile_ids.append(other['id'])
        page.goto(f"{web}/jobs/{job['id']}/apply?profile_id={other['id']}")
        page.get_by_role('link', name='Attach CV to profile').click()
        page.get_by_label('Attach CV', exact=True).set_input_files({'name': 'cv.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': original})
        page.get_by_role('button', name='Save & continue', exact=True).click()
        expect(page).to_have_url(f"{web}/jobs/{job['id']}/apply?profile_id={other['id']}")
        expect(page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        # A blocked Clipboard API should fall back to a gesture-based browser copy.
        page.evaluate("Object.defineProperty(navigator.clipboard, 'writeText', {configurable:true,value:async()=>{throw new Error('Denied')}})")
        with page.expect_popup() as denied_api_popup:
            page.get_by_role('button', name='Copy brief & open ChatGPT').click()
        expect(denied_api_popup.value.get_by_role('heading', name='ChatGPT handoff destination')).to_be_visible()
        expect(page.get_by_role('status').filter(has_text='Brief copied')).to_be_visible()
        copied = page.evaluate('navigator.clipboard.readText()')
        assert 'Alex Example' in copied and job['url'] in copied
        expect(page.get_by_role('textbox', name='Prepared application prompt')).to_have_count(0)
        application_action(page, 'Show brief')
        assert copied == page.get_by_role('textbox', name='Prepared application prompt').input_value()
        denied_api_popup.value.close()

        # Even when ALL copy methods fail, ChatGPT must open, never stay blank.
        page.reload()
        application_action(page, 'Back to brief')
        expect(page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        page.evaluate("""() => {
            Object.defineProperty(navigator.clipboard, 'writeText', {configurable:true,value:async()=>{throw new Error('Denied')}});
            document.execCommand = () => false;
        }""")
        with page.expect_popup() as denied_copy_popup:
            page.get_by_role('button', name='Copy brief & open ChatGPT').click()
        expect(denied_copy_popup.value.get_by_role('heading', name='ChatGPT handoff destination')).to_be_visible()
        expect(page.get_by_role('alert').filter(has_text='Clipboard access')).to_be_visible()
        prompt = page.get_by_role('textbox', name='Prepared application prompt')
        expect(prompt).to_be_visible()
        assert prompt.evaluate('(el) => el.selectionEnd - el.selectionStart') == len(prompt.input_value().encode('utf-16-le')) // 2
        expect(page.get_by_role('textbox', name='Application package', exact=True)).not_to_be_visible()
        denied_copy_popup.value.close()

        # A blocked popup still leaves a usable copy/manual-link path.
        page.reload()
        application_action(page, 'Back to brief')
        expect(page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        page.evaluate("""() => {
            Object.defineProperty(navigator.clipboard, 'writeText', {configurable:true,value:async()=>{throw new Error('Denied')}});
            document.execCommand = () => false;
            window.open = () => null;
        }""")
        page.get_by_role('button', name='Copy brief & open ChatGPT').click()
        expect(page.get_by_role('alert').filter(has_text='Clipboard access')).to_be_visible()
        expect(page.get_by_role('link', name='Open ChatGPT', exact=False)).to_be_visible()
        expect(page.get_by_role('textbox', name='Prepared application prompt')).to_be_visible()
        expect(page.get_by_role('textbox', name='Application package', exact=True)).not_to_be_visible()
        assert not errors, errors
        print('Saved CV upload/persistence, one-button page, automatic CV handoff, DOCX/PDF template export, response validation, fallback and responsive checks passed')
    finally:
        for profile_id in profile_ids:
            context.request.delete(f'{api}/api/v1/profiles/{profile_id}')
        browser.close()
