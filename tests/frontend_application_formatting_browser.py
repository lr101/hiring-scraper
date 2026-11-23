"""Exercise repeated formatting failures through the real UI, worker and exports.

Uses synthetic model responses and an isolated SQLite DB. No ChatGPT requests.
Run after frontend build: .venv/bin/python tests/frontend_application_formatting_browser.py
"""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader
import uvicorn

from codex_fixture import write_codex_fixture


def main():
    with tempfile.TemporaryDirectory(prefix='radius-formatting-browser-') as folder:
        root = Path(folder)
        # Disposable, migrated PostgreSQL test database (see tests/pg.py).
        os.environ['DATABASE_URL'] = os.environ.get('TEST_POSTGRES_URL', '')
        import pg
        pg.reset()
        os.environ['RADIUS_CODEX_STATE_DIR'] = folder
        os.environ['RADIUS_CODEX_BIN'] = str(write_codex_fixture(folder))
        os.environ.pop('OPENAI_API_KEY', None)
        os.environ['APPLICATION_AI_MODEL'] = 'synthetic-test-model'
        (root / 'home').mkdir()
        (root / 'home' / 'auth.json').write_text('{"auth_mode":"chatgpt"}')
        from hiring_scraper.app import api, application_generation as generation, models
        from hiring_scraper.app.database import SessionLocal, engine
        from hiring_scraper.app.tex_cv import validate_tailored_cv
        from test_tex_cv import source
        with SessionLocal.begin() as session:
            company = models.Company(name='Synthetic Labs', source='test', source_id='formatting-browser')
            feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://example.invalid/jobs')
            job = models.Job(feed=feed, external_id='42', title='Research engineer',
                description='Build research tools.', url='https://example.invalid/jobs/42')
            session.add(job)
            session.flush()
            job_id = job.id
        original = source().replace('\\begin{itemize}\n\\item Built research tools.\n\\end{itemize}',
            '\\newcommand{\\RadiusWorkOne}{Built research tools.}\n'
            '\\newcommand{\\RadiusWorkTwo}{Designed useful tools.}')
        original = original.replace('% radius:end', '% radius:end\n\\begin{itemize}\n'
            '\\item \\RadiusWorkOne\n\\item \\RadiusWorkTwo\n\\end{itemize}')
        original = original.strip()  # CV upload normalizes surrounding whitespace.
        expected_cv = original.replace('Built research tools.', 'Built useful tools.')
        expected_letter = 'Dear team,\n\nI build research tools.'
        calls, final_packages = [], []

        def provider(prompt, schema, **kwargs):
            title = schema['title']
            calls.append(title)
            if title == 'RequirementAssessment':
                return {'requirements': []}
            if title == 'CVEditCorrection':
                # Both the initial correction and revision correction fail again.
                name = 'RadiusWorkTwo' if calls.count(title) == 1 else 'RadiusWorkOne'
                return {'cv_edits': [{'name': name, 'text': 'Still too long ' * 100}]}
            if title == 'LetterCorrection':
                return {'cover_letter': r'Dear \unsupported{team}, I build tools.'}
            if title == 'ApplicationReview':
                return {'recommendation': 'possible', 'summary': 'Relevant tools experience.',
                    'requirements': [{'requirement': 'Research tools', 'priority': 'required',
                        'status': 'supported', 'evidence': 'The original CV says Built research tools.'}],
                    'findings': [], 'keywords': []}
            if title == 'FinalContentReview':
                package = json.loads(prompt.split('REVISED PACKAGE:\n')[1])
                final_packages.append(package)
                assert package['latex_documents']['cv'] == expected_cv, repr(package['latex_documents']['cv'])
                assert package['cover_letter'] == expected_letter, repr(package['cover_letter'])
                return {'summary': 'The recovered documents reflect the saved sources.', 'findings': []}
            assert title == 'MacroGeneratedDocuments', title
            data = json.JSONDecoder().raw_decode(prompt.split('SOURCE PAYLOAD:\n')[1])[0]
            revision = calls.count(title) == 2
            return {'cv': 'Alex Example\nResearch tools.',
                'cover_letter': (r'Dear \unsupported{team}, I build tools.' if revision else
                    r'\documentclass{letter}\begin{document}Dear \textbf{team},\par I build research tools.\end{document}'),
                'application_answers': 'I build research tools.', 'review_notes': 'Emphasized tools.',
                'cv_edits': ([{'name': 'RadiusWorkOne', 'text': 'Too long ' * 100}] if revision else [
                    {'name': 'RadiusWorkOne', 'text': 'Built useful tools.'},
                    {'name': 'RadiusWorkTwo', 'text': 'Too long ' * 100}]),
                'form_answers': {'form_id': data['application_form']['form_id'],
                    'answers': [{'id': 'q0', 'value': 'I build research tools.'}]}}

        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(api.app, host='127.0.0.1', port=port, log_level='error', loop='asyncio'))
        with patch.object(generation.codex_runtime, 'generate', side_effect=provider):
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 15
                while not server.started and time.monotonic() < deadline:
                    time.sleep(.05)
                assert server.started, 'Isolated browser API did not start'
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'],
                        executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
                    context = browser.new_context(viewport={'width': 1280, 'height': 1000})
                    profile = context.request.post(url + '/api/v1/profiles', data={'name': 'Synthetic applicant',
                        'summary': 'I build research tools.'}).json()
                    profile_id = profile['id']
                    uploaded = context.request.put(f'{url}/api/v1/profiles/{profile_id}/cv?filename=cv-en.tex&language=en',
                        data=original.encode(), headers={'Content-Type': 'application/x-tex'})
                    assert uploaded.ok, uploaded.text()
                    page = context.new_page()
                    page.set_default_timeout(15000)
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'{url}/jobs/{job_id}/apply?profile_id={profile_id}')
                    captured = {'format': 'radius-form-v1', 'job_id': job_id, 'profile_id': profile_id,
                        'form_id': 'b' * 64, 'url': 'https://example.invalid/apply', 'captured_at': int(time.time() * 1000),
                        'fields': [{'id': 'q0', 'label': 'Motivation', 'type': 'textarea', 'required': True,
                            'max_length': 100, 'options': [], 'accept': '', 'document_kind': '', 'manual': False}]}
                    page.evaluate('''([key, form]) => {localStorage.setItem(key, JSON.stringify(form));
                        document.dispatchEvent(new Event('radius-form-updated'))}''',
                        [f'radius:application-form:v1:{job_id}:{profile_id}', captured])
                    page.get_by_role('button', name='Prepare documents', exact=True).click()
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible(timeout=45000)
                    assert len(final_packages) == 1, calls
                    assert calls.count('CVEditCorrection') == 2, calls
                    assert calls.count('LetterCorrection') == 1, calls
                    expect(page.get_by_role('alert')).to_have_count(0)
                    expect(page.get_by_text('Preparation stopped', exact=False)).to_have_count(0)
                    page.get_by_text('Fit and document checks', exact=True).click()
                    checks = page.get_by_label('Application review report')
                    expect(checks).to_contain_text('Some existing CV wording was retained')
                    expect(checks).to_contain_text('The validated cover letter was retained')
                    page.get_by_text('Fit and document checks', exact=True).click()
                    with page.expect_download() as download:
                        page.get_by_role('button', name='Download LaTeX source', exact=True).click()
                    exported_cv = Path(download.value.path()).read_text()
                    assert exported_cv == expected_cv
                    validate_tailored_cv(original, exported_cv)
                    with page.expect_download() as download:
                        page.get_by_role('button', name='Download PDF', exact=True).click()
                    cv_text = '\n'.join(p.extract_text() for p in PdfReader(download.value.path()).pages)
                    assert 'Built useful tools.' in cv_text, cv_text
                    assert 'Designed useful tools.' in cv_text, cv_text
                    assert 'Too long' not in cv_text, cv_text
                    page.get_by_role('button', name='Cover letter', exact=True).click()
                    expect(page.frame_locator('iframe[title="Cover letter preview"]').locator('body')).to_contain_text('I build research tools.')
                    with page.expect_download() as download:
                        page.get_by_role('button', name='Download PDF', exact=True).click()
                    letter_text = '\n'.join(p.extract_text() for p in PdfReader(download.value.path()).pages)
                    assert 'I build research tools.' in letter_text, letter_text
                    assert 'unsupported' not in letter_text, letter_text
                    job_result = context.request.get(f'{url}/api/v1/profiles/{profile_id}/preparation-job?job_id={job_id}').json()['job']
                    assert job_result['state'] == 'completed', job_result.get('error')
                    assert job_result['result']['package']['form_answers']['answers'] == [{'id': 'q0', 'value': 'I build research tools.'}]
                    page.reload()
                    expect(page.get_by_text('Your documents are ready.', exact=True)).to_be_visible()
                    expect(page.get_by_role('alert')).to_have_count(0)
                    page.set_viewport_size({'width': 390, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
                    assert not errors, errors
                    browser.close()
                    print('Formatting recovery browser: repeated draft/revision failures, safe edits, protected history, letter fallback, final review, source/PDF downloads, form answers and reload all passed.')
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                engine.dispose()


if __name__ == '__main__':
    main()
