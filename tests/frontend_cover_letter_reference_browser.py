"""Language-specific cover-letter reference editing on a preview.

Usage: python tests/frontend_cover_letter_reference_browser.py WEB_URL API_URL
"""
import sys
from playwright.sync_api import sync_playwright, expect
from test_tex_cv import source

web, api = (value.rstrip('/') for value in sys.argv[1:3])
english = 'Dear Hiring Team,\nEnglish reference: a direct opening and concrete example.\nYours sincerely,\nAlex'
german = 'Sehr geehrtes Team,\nDeutsche Vorlage: direkter Einstieg und konkretes Beispiel.\nMit freundlichen Grüßen\nAlex'
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 1280, 'height': 1000})
    result = context.request.post(api + '/api/v1/profiles', data={'name': 'Cover letter reference test'})
    assert result.ok, result.text()
    pid = result.json()['id']
    try:
        for language in ['en', 'de']:
            uploaded = context.request.put(f'{api}/api/v1/profiles/{pid}/cv?filename=cv-{language}.tex&language={language}', data=source(language).encode(), headers={'Content-Type':'application/octet-stream'})
            assert uploaded.ok, uploaded.text()
        page = context.new_page()
        page.goto(f'{web}/profile/{pid}/edit')
        page.get_by_role('textbox', name='Example cover letter (English)', exact=True).fill(english)
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        page.get_by_role('textbox', name='Beispielanschreiben (Deutsch)', exact=True).fill(german)
        page.get_by_role('tab', name='English', exact=True).click()
        expect(page.get_by_role('textbox', name='Example cover letter (English)', exact=True)).to_have_value(english)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved.')
        page.reload()
        expect(page.get_by_role('textbox', name='Example cover letter (English)', exact=True)).to_have_value(english)
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        expect(page.get_by_role('textbox', name='Beispielanschreiben (Deutsch)', exact=True)).to_have_value(german)
        page.get_by_role('textbox', name='Beispielanschreiben (Deutsch)', exact=True).locator('..').screenshot(path='/tmp/cover-letter-reference-field.png')
        saved = next(item for item in context.request.get(api + '/api/v1/profiles').json()['items'] if item['id'] == pid)
        assert saved['localized_content']['en']['application_personalization']['cover_letter_example'] == english
        assert saved['localized_content']['de']['application_personalization']['cover_letter_example'] == german
        # Clearing one language leaves the other language's saved example intact.
        page.goto(f'{web}/profile/{pid}/edit')
        page.get_by_role('textbox', name='Example cover letter (English)', exact=True).fill('')
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved.')
        saved = next(item for item in context.request.get(api + '/api/v1/profiles').json()['items'] if item['id'] == pid)
        assert saved['localized_content']['en']['application_personalization']['cover_letter_example'] == ''
        assert saved['localized_content']['de']['application_personalization']['cover_letter_example'] == german
        print('PASS: English/German reference letters save independently, reload, and clear independently.')
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
