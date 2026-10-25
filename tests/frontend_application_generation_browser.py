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
                    page.goto(f'{url}/jobs/{job_id}/apply?profile_id={profile_id}')
                    direct = page.get_by_role('button', name='Prepare inside Radius', exact=True)
                    expect(direct).to_be_disabled()
                    page.get_by_role('button', name='Continue with ChatGPT', exact=True).click()
                    expect(page.get_by_label('ChatGPT sign-in code')).to_have_text('ABCD-EFGH')
                    expect(page.get_by_role('link', name='Open ChatGPT sign-in', exact=True)).to_have_attribute('href', 'https://auth.openai.com/codex/device')
                    (Path(folder)/'allow-login').touch()
                    expect(direct).to_be_enabled(timeout=15000)
                    expect(page.get_by_text('Using your ChatGPT subscription', exact=True)).to_be_visible()
                    # This is the companion's actual capture shape, including document_kind.
                    captured = {'format': 'radius-form-v1', 'job_id': job_id, 'profile_id': profile_id,
                        'form_id': 'a' * 64, 'url': 'https://example.invalid/apply', 'captured_at': int(time.time() * 1000),
                        'fields': [{'id': 'q0', 'label': 'Motivation', 'type': 'textarea', 'required': True,
                            'max_length': 100, 'options': [], 'accept': '', 'document_kind': '', 'manual': False}]}
                    page.evaluate('''([key, form]) => {localStorage.setItem(key, JSON.stringify(form));
                        document.dispatchEvent(new Event('radius-form-updated'))}''',
                        [f'radius:application-form:v1:{job_id}:{profile_id}', captured])
                    expect(page.get_by_text('This application step’s questions and answer limits are included in your brief.')).to_be_visible()
                    state['mode'] = 'slow'
                    with page.expect_response(lambda response: response.url.endswith('/prepare-application')) as response:
                        direct.click()
                        page.wait_for_timeout(100)
                        # Unchanged companion heartbeats must not cancel a pending generation.
                        page.evaluate("document.dispatchEvent(new Event('radius-form-updated'))")
                    state['mode'] = 'normal'
                    assert response.value.ok, response.value.text()
                    assert response.value.headers.get('cache-control') == 'no-store', 'Generated private documents must not be cached'
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
                    assert state['stages'][:3] == ['GeneratedDocuments', 'ApplicationReview', 'GeneratedDocuments']
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
                    state['mode'] = 'invalid'; direct.click()
                    expect(page.get_by_role('alert').filter(has_text='changed CV facts')).to_be_visible()
                    state['mode'] = 'slow'; direct.click()
                    page.get_by_role('button', name='Cancel preparation', exact=True).click()
                    state['mode'] = 'normal'
                    page.get_by_text('Options', exact=True).click()
                    page.get_by_role('combobox', name='Document language', exact=True).select_option('German')
                    expect(page.locator('[data-original-cv-text]')).to_have_attribute('data-original-cv-text', source('de').strip())
                    expect(direct).to_be_enabled()
                    with page.expect_response(lambda response: response.url.endswith('/prepare-application')
                            and response.request.post_data_json.get('language') == 'de') as german_response:
                        direct.click()
                    assert german_response.value.ok, german_response.value.text()
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
                    # The cancelled English workflow can continue in the background.
                    # Verify this request's result rather than global completion order.
                    assert state['languages'].count('de') == 3
                    assert 'Forschungswerkzeuge entwickelt.' in german_response.value.json()['package']['latex_documents']['cv']
                    # Cancellation must not let the old, slower result overwrite a new generation.
                    page.wait_for_timeout(1200)
                    page.get_by_text('Edit LaTeX source', exact=True).click()
                    expect(page.get_by_role('textbox', name='CV LaTeX source', exact=True)).to_contain_text('Forschungswerkzeuge entwickelt.')
                    back_to_prepare()
                    page.get_by_role('button', name='Disconnect ChatGPT', exact=True).click()
                    expect(direct).to_be_disabled()
                    expect(page.get_by_role('button', name='Copy brief & open ChatGPT ↗', exact=True)).to_be_enabled()
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.screenshot(path='/tmp/radius-direct-preparation-mobile.png', full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile layout overflow'
                    assert not errors, errors
                    browser.close()
                    print('Direct preparation browser: real UI/API, source download, recovery, provider errors, history rejection, cancellation, German selection and disabled fallback passed.')
            finally:
                server.should_exit = True; thread.join(timeout=10)
                engine.dispose()


if __name__ == '__main__':
    main()
