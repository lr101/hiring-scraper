"""Real Radius UI/API with synthetic applicant data, Codex login and model output.

Run after frontend build: .venv/bin/python tests/frontend_application_generation_browser.py
"""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from unittest.mock import patch
from fastapi import HTTPException
from codex_fixture import write_codex_fixture

from playwright.sync_api import expect, sync_playwright
import uvicorn


def main():
    with tempfile.TemporaryDirectory(prefix='radius-generation-browser-') as folder:
        os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(folder) / 'application.sqlite3')
        executable = write_codex_fixture(folder)
        os.environ['RADIUS_CODEX_STATE_DIR'] = folder
        os.environ['RADIUS_CODEX_BIN'] = str(executable)
        os.environ.pop('OPENAI_API_KEY', None)
        os.environ['APPLICATION_AI_MODEL'] = 'synthetic-test-model'
        from hiring_scraper.app import api, application_generation as generation, models
        from hiring_scraper.app.database import SessionLocal, engine
        from hiring_scraper.app.latex_documents import latex_source
        from test_tex_cv import source
        models.Base.metadata.create_all(engine)
        with SessionLocal.begin() as session:
            company = models.Company(name='Synthetic Product GmbH', source='test', source_id='direct-browser')
            feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://example.invalid/jobs')
            job = models.Job(feed=feed, external_id='42', title='Product coordinator',
                             description='Coordinate product requirements in an English-speaking team.',
                             url='https://example.invalid/jobs/42')
            session.add(job); session.flush(); job_id = job.id
        state = {'mode': 'normal', 'calls': 0, 'languages': [], 'stages': []}

        def provider(prompt, schema, **kwargs):
            state['calls'] += 1
            state['stages'].append(schema['title'])
            mode = state['mode']
            data = json.JSONDecoder().raw_decode(prompt.split('SOURCE PAYLOAD:\n')[1])[0]
            state['languages'].append(data['writing_preferences']['document_language'])
            if mode == 'error':
                raise HTTPException(502, 'ChatGPT could not prepare this application. Check subscription usage.')
            if mode == 'slow': time.sleep(1)
            if schema['title'] == 'RevisionOutput':
                document = data['original_cv_text'] if 'DOCUMENT TYPE: CV LaTeX source' in prompt else 'Dear hiring team,\nI bring practical requirements coordination.'
                return {'document': document, 'review': 'The original can be more direct.', 'changes': 'Made the wording more direct.'}
            if schema['title'] == 'ApplicationReview':
                return {'recommendation': 'possible', 'summary': 'Coordination matches; permit status is unknown.',
                    'requirements': [{'requirement': 'German work authorization', 'priority': 'logistics',
                        'status': 'unknown', 'evidence': 'Work permission has not been saved.'}],
                    'findings': [{'document': 'cover_letter', 'category': 'coverage',
                        'issue': 'Lead with requirements coordination.', 'suggestion': 'Use the saved research-tools evidence.'}],
                    'keywords': [{'term': 'requirements', 'priority': 'required', 'status': 'supported',
                        'evidence_source': 'profile', 'evidence_quote': 'Product requirements coordination'}]}
            cv = data['original_cv_text'].replace('Built research tools.', 'Coordinated research-tool requirements.').rstrip() + '\n'
            if mode == 'invalid': cv = cv.replace('"2021"', '"2001"')
            documents = {'cv': 'Alex Example\nCoordinated research-tool requirements.',
                         'cover_letter': 'Dear hiring team,\nI coordinate requirements and research tools.',
                         'application_answers': 'No additional answers needed.', 'review_notes': 'Emphasized coordination.',
                         'latex_documents': {'cv': cv, 'cover_letter': latex_source('Unreviewed invented achievements.')},
                         'form_answers': {'form_id': data['application_form']['form_id'], 'answers': [
                             {'id': 'q0', 'value': 'Requirements coordination.'}]} if data.get('application_form') else None}
            return documents

        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(api.app, host='127.0.0.1', port=port, log_level='error', loop='asyncio'))
        with patch.object(generation.codex_runtime, 'generate', side_effect=provider):
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            try:
                deadline = time.monotonic() + 15
                while not server.started and time.monotonic() < deadline: time.sleep(.05)
                assert server.started, 'Isolated browser API did not start'
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'],
                        executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
                    context = browser.new_context(viewport={'width': 1280, 'height': 1000})
                    profile = context.request.post(url + '/api/v1/profiles', data={'name': 'Synthetic applicant',
                        'summary': 'Product requirements coordination', 'language_levels': {'German': 'B2'}}).json()
                    profile_id = profile['id']
                    for language in ('en', 'de'):
                        uploaded = context.request.put(f'{url}/api/v1/profiles/{profile_id}/cv?filename=cv-{language}.tex&language={language}',
                            data=source(language).encode(), headers={'Content-Type': 'application/x-tex'})
                        assert uploaded.ok, uploaded.text()
                    page = context.new_page(); page.set_default_timeout(15000)
                    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                    preparation_posts = []
                    page.on('request', lambda request: preparation_posts.append(request.url)
                        if request.method == 'POST' and request.url.split('?', 1)[0].endswith('/preparation-job') else None)
                    page.goto(f'{url}/jobs/{job_id}/apply?profile_id={profile_id}')
                    expect(page.get_by_role('button', name='Continue with ChatGPT', exact=True)).to_be_visible()
                    expect(page.get_by_role('combobox', name='Brief type', include_hidden=True)).to_be_hidden()
                    expect(page.get_by_role('link', name='Change CV', include_hidden=True)).to_be_hidden()
                    direct = page.get_by_role('button', name='Prepare documents', exact=True)
                    page.get_by_role('button', name='Continue with ChatGPT', exact=True).click()
                    expect(page.get_by_label('ChatGPT sign-in code')).to_have_text('ABCD-EFGH')
                    expect(page.get_by_role('link', name='Open ChatGPT sign-in', exact=True)).to_have_attribute('href', 'https://auth.openai.com/codex/device')
                    # This is the companion's actual capture shape, including document_kind.
                    captured = {'format': 'radius-form-v1', 'job_id': job_id, 'profile_id': profile_id,
                        'form_id': 'a' * 64, 'url': 'https://example.invalid/apply', 'captured_at': int(time.time() * 1000),
                        'fields': [{'id': 'q0', 'label': 'Motivation', 'type': 'textarea', 'required': True,
                            'max_length': 100, 'options': [], 'accept': '', 'document_kind': '', 'manual': False}]}
                    page.evaluate('''([key, form]) => {localStorage.setItem(key, JSON.stringify(form));
                        document.dispatchEvent(new Event('radius-form-updated'))}''',
                        [f'radius:application-form:v1:{job_id}:{profile_id}', captured])
                    page.get_by_text('Options', exact=True).click()
                    expect(page.get_by_role('combobox', name='Brief type')).to_be_visible()
                    expect(page.get_by_role('link', name='Change CV')).to_be_visible()
                    expect(page.get_by_text('This application step’s questions and answer limits are included in your brief.')).to_be_visible()
                    page.get_by_text('Options', exact=True).click()
                    state['mode'] = 'slow'
                    (Path(folder)/'allow-login').touch()
                    expect(direct).to_be_enabled(timeout=15000)
                    settings = context.new_page()
                    settings.goto(f'{url}/settings')
                    expect(settings.get_by_text('Default model: synthetic-test-model', exact=False)).to_be_visible()
                    settings.get_by_role('combobox', name='Model', exact=True).select_option('gpt-6-luna')
                    settings.get_by_role('button', name='Save model', exact=True).click()
                    expect(settings.get_by_text('Current default: gpt-6-luna', exact=True)).to_be_visible()
                    expect(settings.get_by_text('Default model: gpt-6-luna', exact=False)).to_be_visible()
                    settings.close()
                    page.wait_for_timeout(300)
                    assert not preparation_posts, 'Loading the application page must not start preparation'
                    with page.expect_response(lambda response: response.url.endswith('/preparation-job') and response.request.method == 'POST', timeout=30000) as response:
                        direct.click()
                        # Unchanged companion heartbeats must not cancel a pending generation.
                        page.evaluate("document.dispatchEvent(new Event('radius-form-updated'))")
                    assert response.value.ok, response.value.text()
                    assert response.value.headers.get('cache-control') == 'no-store', 'Generated private documents must not be cached'
                    expect(page.get_by_text('You can leave this page', exact=False)).to_be_visible()
                    page.reload()
                    expect(page.get_by_text('You can leave this page', exact=False)).to_be_visible()
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible(timeout=30000)
                    state['mode'] = 'normal'
                    assert state['stages'][:3] == ['GeneratedDocuments', 'ApplicationReview', 'GeneratedDocuments']
                    page.get_by_role('button', name='Cover letter', exact=True).click()
                    page.get_by_label('What would you like to change?').fill('Make the opening more direct.')
                    page.get_by_role('button', name='Review and suggest changes', exact=True).click()
                    expect(page.get_by_text('Suggested version', exact=True)).to_be_visible(timeout=30000)
                    expect(page.frame_locator('iframe[title="Suggested cover letter"]').locator('body')).to_contain_text('I bring practical requirements coordination.')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    comparison = page.get_by_role('group', name='Compare cover letters')
                    expect(comparison.get_by_role('button', name='Suggested')).to_have_attribute('aria-pressed', 'true')
                    expect(page.get_by_title('Suggested cover letter')).to_be_visible()
                    expect(page.get_by_title('Current cover letter')).to_be_hidden()
                    comparison.get_by_role('button', name='Current').click()
                    expect(page.get_by_title('Current cover letter')).to_be_visible()
                    expect(page.get_by_title('Suggested cover letter')).to_be_hidden()
                    page.set_viewport_size({'width': 1280, 'height': 1000})
                    page.screenshot(path='/tmp/radius-revision-review.png', full_page=True)
                    page.reload()
                    page.get_by_role('button', name='Open it', exact=True).click()
                    expect(page.get_by_text('Suggested version', exact=True)).to_be_visible(timeout=15000)
                    page.get_by_text('Edit document text', exact=True).click()
                    page.get_by_role('textbox', name='Cover letter text').fill('Dear team, edited while the suggestion was waiting.')
                    expect(page.get_by_role('button', name='Use this version', exact=True)).to_be_disabled()
                    page.get_by_role('textbox', name='Cover letter text').fill('Dear hiring team,\nI coordinate requirements and research tools.')
                    page.get_by_text('Edit document text', exact=True).click()
                    expect(page.frame_locator('iframe[title="Cover letter preview"]').locator('body')).to_contain_text('I coordinate requirements and research tools.')
                    def reject_save(route):
                        if route.request.method == 'PUT':
                            route.fulfill(status=409, json={'detail': 'Test save conflict: reload the saved draft.'})
                        else:
                            route.continue_()
                    page.route(f'{url}/api/v1/profiles/{profile_id}/application-draft?*', reject_save)
                    page.get_by_role('button', name='Use this version', exact=True).click()
                    expect(page.get_by_role('region', name='Review and revise cover letter').get_by_role('alert').filter(has_text='Test save conflict')).to_be_visible()
                    expect(page.frame_locator('iframe[title="Cover letter preview"]').locator('body')).to_contain_text('I coordinate requirements and research tools.')
                    page.unroute(f'{url}/api/v1/profiles/{profile_id}/application-draft?*', reject_save)
                    page.get_by_role('button', name='Keep this device’s draft').click()
                    page.get_by_role('button', name='Use this version', exact=True).click()
                    expect(page.frame_locator('iframe[title="Cover letter preview"]').locator('body')).to_contain_text('I bring practical requirements coordination.')
                    retained = context.request.get(f'{url}/api/v1/profiles/{profile_id}/document-revision?job_id={job_id}').json()['job']
                    assert retained['state'] == 'completed', 'Accepted suggestion must remain recoverable'
                    page.reload()
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
                    page.wait_for_timeout(1500)
                    if page.get_by_role('button', name='Keep this device’s draft', exact=True).is_visible(): page.get_by_role('button', name='Keep this device’s draft', exact=True).click()
                    page.get_by_role('button', name='Cover letter', exact=True).click()
                    expect(page.frame_locator('iframe[title="Cover letter preview"]').locator('body')).to_contain_text('I bring practical requirements coordination.')
                    page.get_by_role('button', name='CV', exact=True).click()
                    page.get_by_label('What would you like to change?').fill('Keep the original wording.')
                    page.get_by_role('button', name='Review and suggest changes', exact=True).click()
                    expect(page.get_by_text('Suggested version', exact=True)).to_be_visible(timeout=30000)
                    page.get_by_role('button', name='Use this version', exact=True).click()
                    page.get_by_text('Edit LaTeX source', exact=True).click()
                    expect(page.get_by_role('textbox', name='CV LaTeX source', exact=True)).to_contain_text('Built research tools.')
                    page.wait_for_function('''key => { const draft = JSON.parse(localStorage.getItem(key) || 'null'); return draft?.package?.cv === 'The current CV is available in its LaTeX source and PDF.' && draft?.package?.latex_documents?.cv?.includes('Built research tools.') }''', arg=f'radius:application-draft:v1:{job_id}:{profile_id}', timeout=5000)
                    cv_editor = page.get_by_role('textbox', name='CV LaTeX source', exact=True)
                    cv_editor.fill(cv_editor.input_value().replace('Built research tools.', 'Built reliable tools.'))
                    page.wait_for_function('''key => { const draft = JSON.parse(localStorage.getItem(key) || 'null'); return draft?.package?.cv === 'The current CV is available in its LaTeX source and PDF.' && draft?.package?.latex_documents?.cv?.includes('Built reliable tools.') }''', arg=f'radius:application-draft:v1:{job_id}:{profile_id}', timeout=5000)
                    valid_source = cv_editor.input_value()
                    cv_editor.fill('')
                    expect(page.get_by_role('alert').filter(has_text='CV source is incomplete')).to_be_visible()
                    page.wait_for_timeout(1200)
                    saved_source = page.evaluate('''key => JSON.parse(localStorage.getItem(key)).package.latex_documents.cv''', f'radius:application-draft:v1:{job_id}:{profile_id}')
                    assert saved_source == valid_source, 'Incomplete CV must not replace the last valid draft'
                    cv_editor.fill(valid_source)
                    expect(page.get_by_role('alert').filter(has_text='CV source is incomplete')).to_have_count(0)
                    page.reload()
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
                    page.wait_for_timeout(1500)
                    if page.get_by_role('button', name='Keep this device’s draft', exact=True).is_visible(): page.get_by_role('button', name='Keep this device’s draft', exact=True).click()
                    page.get_by_text('Edit LaTeX source', exact=True).click()
                    expect(page.get_by_role('textbox', name='CV LaTeX source', exact=True)).to_contain_text('Built reliable tools.')
                    page.get_by_text('Edit LaTeX source', exact=True).click()
                    # Restore the original synthetic generation for the existing export checks.
                    page.get_by_text('Options', exact=True).click()
                    page.get_by_role('button', name='Back to brief', exact=True).click()
                    page.get_by_role('button', name='Prepare documents', exact=True).click()
                    expect(page.get_by_role('button', name='Open latest prepared documents')).to_be_visible(timeout=30000)
                    page.get_by_role('button', name='Open latest prepared documents').click()
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
                    page.get_by_text('Fit and document checks', exact=True).click()
                    checks = page.get_by_label('Application review report')
                    expect(checks).to_contain_text('German work authorization')
                    expect(checks).to_contain_text('unknown')
                    expect(checks).to_contain_text('PDF checks:')
                    expect(checks).to_contain_text('requirements (required): appears literally in PDF')
                    page.get_by_text('Fit and document checks', exact=True).click()
                    expect(page.get_by_role('button', name='Download PDF', exact=True)).to_be_enabled()
                    page.get_by_text('Edit LaTeX source', exact=True).click()
                    expect(page.get_by_role('textbox', name='CV LaTeX source', exact=True)).to_contain_text('Coordinated research-tool requirements.')
                    with page.expect_download() as download:
                        page.get_by_role('button', name='Download LaTeX source', exact=True).click()
                    assert 'Coordinated research-tool requirements.' in Path(download.value.path()).read_text()
                    assert len(context.pages) == 1, 'Direct preparation must not open ChatGPT'
                    page.get_by_text('Edit LaTeX source', exact=True).click()
                    expect(page.locator('.template-pdf-preview canvas').first).to_be_visible(timeout=30000)
                    page.get_by_role('button', name='Cover letter', exact=True).click()
                    expect(page.frame_locator('iframe[title="Cover letter preview"]').locator('body')).to_contain_text('I coordinate requirements and research tools.')
                    with page.expect_download() as letter_download:
                        page.get_by_role('button', name='Download PDF', exact=True).click()
                    from pypdf import PdfReader
                    letter_text = '\n'.join(p.extract_text() for p in PdfReader(letter_download.value.path()).pages)
                    assert 'I coordinate requirements and research tools.' in letter_text
                    assert 'Unreviewed invented achievements.' not in letter_text
                    page.get_by_text('Edit document text', exact=True).click()
                    editor = page.get_by_role('textbox', name='Cover letter text')
                    editor.fill('Dear team, I reviewed and edited this letter.')
                    endpoint = f'{url}/api/v1/profiles/{profile_id}/application-draft?job_id={job_id}'
                    deadline = time.time() + 15
                    while time.time() < deadline:
                        saved = context.request.get(endpoint).json().get('saved')
                        if saved and 'reviewed and edited' in json.dumps(saved): break
                        time.sleep(.2)
                    assert saved and 'reviewed and edited' in json.dumps(saved), 'Edited draft was not synced'
                    second = browser.new_context(viewport={'width': 1280, 'height': 1000})
                    other = second.new_page(); other.goto(f'{url}/jobs/{job_id}/apply?profile_id={profile_id}')
                    expect(other.get_by_text('Your documents are ready.', exact=True)).to_be_visible(timeout=15000)
                    other.get_by_role('button', name='Cover letter', exact=True).click()
                    expect(other.frame_locator('iframe[title="Cover letter preview"]').locator('body')).to_contain_text('I reviewed and edited this letter.')
                    other.get_by_text('Edit document text', exact=True).click()
                    other.get_by_role('textbox', name='Cover letter text').fill('Dear team, edited on the second device.')
                    deadline = time.time() + 15
                    while time.time() < deadline:
                        saved = context.request.get(endpoint).json().get('saved')
                        if saved and 'edited on the second device' in json.dumps(saved): break
                        time.sleep(.2)
                    assert saved and 'edited on the second device' in json.dumps(saved)
                    second.close()
                    editor.fill('Dear team, I coordinate requirements and research tools.')
                    expect(page.get_by_text('This draft changed on another device.', exact=False)).to_be_visible(timeout=15000)
                    page.get_by_role('button', name='Keep this device’s draft', exact=True).click()
                    deadline = time.time() + 15
                    while time.time() < deadline:
                        saved = context.request.get(endpoint).json().get('saved')
                        if saved and 'I coordinate requirements and research tools.' in json.dumps(saved): break
                        time.sleep(.2)
                    assert saved and 'I coordinate requirements and research tools.' in json.dumps(saved)
                    page.get_by_text('Edit document text', exact=True).click()
                    page.get_by_role('button', name='CV', exact=True).click()
                    page.screenshot(path='/tmp/radius-direct-preparation-desktop.png', full_page=True)
                    page.reload()
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
                    def back_to_prepare():
                        page.get_by_text('Options', exact=True).click()
                        page.get_by_role('button', name='Back to brief', exact=True).click()
                    back_to_prepare()
                    state['mode'] = 'error'; direct.click()
                    expect(page.get_by_role('alert').filter(has_text='ChatGPT could not prepare')).to_be_visible()
                    assert 'Private provider details' not in page.content()
                    state['mode'] = 'invalid'; page.get_by_text('Options', exact=True).click(); page.get_by_role('button', name='Start with current options', exact=True).click()
                    expect(page.get_by_role('alert').filter(has_text='protected-history checks')).to_be_visible()
                    state['mode'] = 'normal'
                    page.get_by_text('Options', exact=True).click()
                    page.get_by_role('combobox', name='Document language', exact=True).select_option('German')
                    expect(page.locator('[data-original-cv-text]')).to_have_attribute('data-original-cv-text', source('de').strip())
                    page.get_by_role('button', name='Start with current options', exact=True).click()
                    expect(page.get_by_role('button', name='Open latest prepared documents')).to_be_visible(timeout=30000)
                    page.get_by_role('button', name='Open latest prepared documents').click()
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
                    assert state['languages'].count('de') == 3
                    page.get_by_text('Edit LaTeX source', exact=True).click()
                    expect(page.get_by_role('textbox', name='CV LaTeX source', exact=True)).to_contain_text('Forschungswerkzeuge entwickelt.')
                    back_to_prepare()
                    disconnected = context.request.post(f'{url}/api/v1/application-preparation/logout', data={})
                    assert disconnected.ok, disconnected.text()
                    page.reload()
                    if page.get_by_text('Your documents are ready.', exact=True).is_visible(): back_to_prepare()
                    expect(page.get_by_role('button', name='Continue with ChatGPT', exact=True)).to_be_visible()
                    page.get_by_text('Options', exact=True).click()
                    page.once('dialog', lambda dialog: dialog.accept())
                    page.get_by_role('button', name='Delete saved application', exact=True).click()
                    expect(page.get_by_text('Saved application deleted from Radius and this browser.', exact=False)).to_be_visible()
                    expect(page.get_by_text('This application step’s questions and answer limits are included in your brief.')).to_have_count(0)
                    assert page.evaluate('key => localStorage.getItem(key)', f'radius:application-form:v1:{job_id}:{profile_id}') is None
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.screenshot(path='/tmp/radius-direct-preparation-mobile.png', full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile layout overflow'
                    assert not errors, errors
                    browser.close()
                    print('Direct preparation browser: real UI/API, source download, recovery, provider errors, history rejection, reload recovery, German selection and disabled fallback passed.')
            finally:
                server.should_exit = True; thread.join(timeout=10)
                engine.dispose()


if __name__ == '__main__':
    main()
