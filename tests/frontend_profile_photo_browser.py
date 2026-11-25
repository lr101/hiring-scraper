"""Save, replace and remove a portrait in profile setup.

Run against an isolated preview with APPLICATION_TEST_WEB_URL and APPLICATION_TEST_API_URL.
"""
from io import BytesIO
import os

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from test_tex_cv import source

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')


def png(color):
    output = BytesIO()
    Image.new('RGB', (48, 64), color).save(output, format='PNG')
    return output.getvalue()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    context = browser.new_context()
    page = context.new_page()
    pid = None
    portrait, replacement = png('#355867'), png('#784435')
    try:
        page.goto(f'{web}/profile/new')
        page.get_by_label('Profile name', exact=True).fill('Profile photo browser test')
        page.get_by_role('navigation', name='Profile setup').get_by_role('button', name='CVs & writing', exact=False).click()
        page.get_by_label('Profile photo', exact=True).set_input_files({'name': 'portrait.png', 'mimeType': 'image/png', 'buffer': portrait})
        expect(page.get_by_alt_text('Profile photo preview')).to_be_visible()
        page.get_by_label('English base CV', exact=True).set_input_files({'name': 'cv-en.tex', 'mimeType': 'application/x-tex', 'buffer': source().encode()})
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        expect(page.get_by_alt_text('Profilfoto-Vorschau')).to_be_visible()
        page.get_by_label('German base CV', exact=True).set_input_files({'name': 'cv-de.tex', 'mimeType': 'application/x-tex', 'buffer': source('de').encode()})
        page.get_by_role('button', name='Save progress', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved.')
        pid = int(page.url.split('/profile/')[1].split('/')[0])
        assert context.request.get(f'{api}/api/v1/profiles/{pid}/photo').body() == portrait
        page.reload()
        expect(page.get_by_alt_text('Profile photo preview')).to_be_visible()
        assert page.get_by_alt_text('Profile photo preview').evaluate('(img) => img.complete && img.naturalWidth > 0')
        # Invalid replacements leave the saved portrait available.
        page.get_by_label('Profile photo', exact=True).set_input_files({'name': 'broken.png', 'mimeType': 'image/png', 'buffer': b'broken'})
        expect(page.get_by_role('alert')).to_contain_text('Could not read this PNG photo.')
        assert context.request.get(f'{api}/api/v1/profiles/{pid}/photo').body() == portrait
        # Removal is staged and can be undone before saving.
        page.get_by_role('button', name='Remove photo', exact=True).click()
        expect(page.get_by_text('Photo will be removed when you save.', exact=False)).to_be_visible()
        assert context.request.get(f'{api}/api/v1/profiles/{pid}/photo').body() == portrait
        page.get_by_role('button', name='Undo', exact=True).click()
        expect(page.get_by_alt_text('Profile photo preview')).to_be_visible()
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved.')
        # The overview uses the saved picture as its avatar.
        page.goto(f'{web}/profile')
        card = page.locator('.saved-profile').filter(has_text='Profile photo browser test')
        expect(card.locator('.saved-profile-avatar img')).to_be_visible()
        # Replace the saved photo, then remove it permanently and verify a reload.
        page.goto(f'{web}/profile/{pid}/edit#profile-documents')
        page.get_by_label('Profile photo', exact=True).set_input_files({'name': 'replacement.png', 'mimeType': 'image/png', 'buffer': replacement})
        expect(page.get_by_text('replacement.png · ready to save', exact=True)).to_be_visible()
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved.')
        assert context.request.get(f'{api}/api/v1/profiles/{pid}/photo').body() == replacement
        page.screenshot(path='/tmp/profile-photo-editor.png', full_page=True)
        page.get_by_role('button', name='Remove photo', exact=True).click()
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved.')
        page.reload()
        expect(page.get_by_alt_text('Profile photo preview')).to_have_count(0)
        assert context.request.get(f'{api}/api/v1/profiles/{pid}/photo').status == 404
        print('New-profile photo, bilingual save, reload, invalid replacement, staged removal, avatar, PDF/ZIP defaults, opt-out, temporary override, saved replacement and permanent removal passed.')
    finally:
        if pid:
            context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
