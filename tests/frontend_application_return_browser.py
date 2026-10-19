"""Exercise actual return links, one-block paste/file imports and browser drafts.
ChatGPT navigation is simulated; no logged-in model or employer is contacted.
"""
import base64
import zlib
import html
import json
import os
import re
import subprocess
from urllib.parse import quote

from playwright.sync_api import sync_playwright, expect
from application_browser_helpers import application_action
from test_cv_template import template_docx

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 390, 'height': 900}, permissions=['clipboard-read', 'clipboard-write'])
    context.route('https://chatgpt.com/**', lambda route: route.fulfill(body='<h1>ChatGPT</h1>', content_type='text/html'))
    profile = context.request.post(f'{api}/api/v1/profiles', data={'name': 'Return-link browser test'}).json()
    profile_id = profile['id']
    original = template_docx()
    try:
        cv = context.request.put(f'{api}/api/v1/profiles/{profile_id}/cv?filename=return-test.docx', data=original, headers={'Content-Type': 'application/octet-stream'}).json()
        # Use the same persisted metadata the application reads (SQLite normalizes dates).
        cv = next(item for item in context.request.get(f'{api}/api/v1/profiles').json()['items'] if item['id'] == profile_id)['cv']
        source = context.request.get(f'{api}/api/v1/profiles/{profile_id}/cv/template').json()
        job = context.request.get(f'{api}/api/v1/jobs?query=projekt&limit=1').json()['items'][0]
        base = f"{web}/jobs/{job['id']}/apply?profile_id={profile_id}"
        paragraph = next(row for row in source['paragraphs'] if row['text'] == 'Coordinate projects')
        package = {'format': 'radius-application-v1', 'job_id': job['id'], 'profile_id': profile_id, 'cv_updated_at': cv['updated_at'],
                   'cv': [{'id': paragraph['id'], 'text': 'Koordination für München – 你好 🚀'}],
                   'cover_letter': 'Dear team,\nA truthful application.', 'application_answers': 'Notice: unknown', 'review_notes': 'Check dates.'}
        payload = json.dumps(package, ensure_ascii=False, separators=(',', ':'))
        link = base + '#applicationz=' + base64.urlsafe_b64encode(zlib.compress(payload.encode(), 9)).decode().rstrip('=')
        page = context.new_page()
        # Install before app timers exist, so expiry timers can be advanced too.
        page.clock.install()
        page.set_default_timeout(15000)
        requests = []
        context.on('request', lambda request: requests.append(request.url))
        # A clicked link must fill the editor and remove document data from the URL.
        page.goto(link)
        expect(page.get_by_role('heading', name='Your documents are ready.', exact=True)).to_be_visible(timeout=25000)
        expect(page).to_have_url(base)
        field = page.get_by_role('textbox', name=f"CV paragraph {paragraph['id'] + 1}", exact=True, include_hidden=True)
        expect(field).to_have_value('Koordination für München – 你好 🚀')
        assert not any('applicationz=' in url or 'application=' in url or 'Koordination' in url for url in requests), 'Return documents leaked into an HTTP request'
        # Edits survive a reload and a new tab, without changing the original CV.
        page.get_by_text('Edit document text', exact=True).click()
        field.fill('My reviewed text')
        page.reload()
        expect(field).to_have_value('My reviewed text')
        second = context.new_page()
        second.goto(base)
        expect(second.get_by_role('textbox', name=f"CV paragraph {paragraph['id'] + 1}", exact=True, include_hidden=True)).to_have_value('My reviewed text')
        second.close()
        assert context.request.get(f'{api}/api/v1/profiles/{profile_id}/cv').body() == original
        # Invalid/wrong-job/stale returns leave the current draft unchanged.
        for changed in [{'job_id': job['id'] + 1}, {'cv_updated_at': 'old'}, {'cv': [{'id': 99999, 'text': 'Bad'}]}]:
            invalid = json.dumps({**package, **changed})
            page.goto(base + '#application=' + quote(invalid, safe=''))
            expect(page.get_by_role('alert')).to_be_visible()
            expect(field).to_have_value('My reviewed text')
            expect(page).to_have_url(base)
        page.goto(base + '#application=%E0%A4%A')
        expect(page.get_by_role('alert')).to_be_visible()
        expect(field).to_have_value('My reviewed text')
        # Import one copied JSON block, including when a whole answer was copied.
        application_action(page, 'Paste a new response')
        page.get_by_role('textbox', name='Application package', exact=True).fill('Your package:\n```json\n' + payload + '\n```\nReturn to Radius.')
        page.get_by_role('button', name='Open documents', exact=True).click()
        expect(field).to_have_value(package['cv'][0]['text'])
        # A normal paste imports immediately: no marker selection or extra submit.
        application_action(page, 'Paste a new response')
        changed = {**package, 'cv': [{'id': paragraph['id'], 'text': 'Imported by paste'}]}
        page.get_by_role('textbox', name='Application package', exact=True).evaluate("""(element, text) => {
            const transfer = new DataTransfer(); transfer.setData('text/plain', text);
            element.dispatchEvent(new ClipboardEvent('paste', {bubbles:true, cancelable:true, clipboardData:transfer}));
        }""", json.dumps(changed))
        expect(field).to_have_value('Imported by paste')
        # Downloaded result JSON is another real import path, with the same validation.
        application_action(page, 'Paste a new response')
        changed['cv'][0]['text'] = 'Imported from file'
        page.get_by_label('Import result file', exact=True).set_input_files({'name': 'application-package.json', 'mimeType': 'application/json', 'buffer': json.dumps(changed).encode()})
        expect(field).to_have_value('Imported from file')
        # The generated prompt's actual Python recipe produces a working return URL.
        application_action(page, 'Clear browser draft')
        expect(page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        with page.expect_popup() as chat:
            page.get_by_role('button', name='Copy brief & open ChatGPT').click()
        expect(chat.value.get_by_role('heading', name='ChatGPT', exact=True)).to_be_visible()
        expect(page.get_by_role('textbox', name='Application package', exact=True)).not_to_be_visible()
        brief = page.evaluate('navigator.clipboard.readText()')
        recipe = re.search(r'RETURN_LINK_RECIPE_BEGIN\n(.*?)\nRETURN_LINK_RECIPE_END', brief, re.S).group(1)
        script = 'application_package = ' + repr(package) + '\n' + recipe
        generated = subprocess.run([os.sys.executable, '-c', script], capture_output=True, text=True, check=True).stdout.strip()
        assert generated == '[Return to Radius with my documents](' + link + ')'
        # Simulate the actual external link click, rather than only URL navigation.
        chat.value.set_content('<a href="' + html.escape(link, quote=True) + '">Return to Radius with my documents</a>')
        chat.value.get_by_role('link', name='Return to Radius with my documents').click()
        expect(chat.value.get_by_role('textbox', name=f"CV paragraph {paragraph['id'] + 1}", exact=True, include_hidden=True)).to_have_value(package['cv'][0]['text'])
        expect(chat.value).to_have_url(base)
        chat.value.close()
        # At typical and unusually long sizes, valid links remain browser-readable.
        for length in [4000, 12000]:
            long_package = {**package, 'cover_letter': 'ü résumé ' * (length // 9)}
            long_link = base + '#application=' + quote(json.dumps(long_package, ensure_ascii=False, separators=(',', ':')), safe='')
            page.goto(long_link)
            expect(field).to_have_value(package['cv'][0]['text'])
            page.get_by_role('group', name='Choose document').get_by_role('button', name='Cover letter', exact=True).click()
            expect(page.get_by_role('textbox', name='Cover letter text', exact=True, include_hidden=True)).to_have_value(long_package['cover_letter'])
        # Clear really removes saved document data, including on reload.
        application_action(page, 'Clear browser draft')
        page.reload()
        expect(page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        for theme in ['light', 'dark']:
            page.evaluate('(theme) => {document.documentElement.dataset.theme = theme}', theme)
            for width in [320, 390, 768, 1440]:
                page.set_viewport_size({'width': width, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (theme, width)
        key = f"radius:application-draft:v1:{job['id']}:{profile_id}"
        # Simply restoring a six-day-old draft must not extend its retention.
        aged = page.evaluate("""({key, package}) => {
            const saved_at = Date.now()-6*86400000;
            localStorage.setItem(key, JSON.stringify({
                context:{job_id:package.job_id,profile_id:package.profile_id,cv_updated_at:package.cv_updated_at},
                package, step:'edit', saved_at
            })); return saved_at;
        }""", {'key': key, 'package': package})
        page.reload()
        expect(field).to_have_value(package['cv'][0]['text'])
        assert page.evaluate('(key) => JSON.parse(localStorage.getItem(key)).saved_at', key) == aged, 'Reload must preserve the expiry deadline'
        second = context.new_page()
        second.goto(base)
        expect(second.get_by_role('textbox', name=f"CV paragraph {paragraph['id'] + 1}", exact=True, include_hidden=True)).to_have_value(package['cv'][0]['text'])
        assert second.evaluate('(key) => JSON.parse(localStorage.getItem(key)).saved_at', key) == aged
        second.close()
        page.clock.fast_forward(86400000 + 1000)
        expect(page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        assert page.evaluate('(key) => localStorage.getItem(key)', key) is None
        # Expired data is deleted, rather than silently left in localStorage.
        page.evaluate("""({key, package}) => localStorage.setItem(key, JSON.stringify({
            context:{job_id:package.job_id,profile_id:package.profile_id,cv_updated_at:package.cv_updated_at},
            package, step:'edit', saved_at:Date.now()-8*86400000
        }))""", {'key': key, 'package': package})
        page.reload()
        expect(page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        assert page.evaluate('(key) => localStorage.getItem(key)', key) is None, 'Expired draft data must be removed'
        # Expiry also works in a tab that stays open, without a navigation.
        application_action(page, 'I already have a result')
        page.wait_for_function('(key) => localStorage.getItem(key) !== null', arg=key)
        page.clock.fast_forward(7 * 86400000 + 1000)
        expect(page.get_by_role('button', name='Copy brief & open ChatGPT')).to_be_enabled()
        assert page.evaluate('(key) => localStorage.getItem(key)', key) is None
        print('Return-link Unicode round trip, URL privacy, source binding, invalid-return draft preservation, cross-tab/reload drafts, single-block paste, file import, prompt Python recipe, external link click and mobile checks passed')
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{profile_id}')
        browser.close()
