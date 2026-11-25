"""Exercise onboarding, validation across sections, persistence, and direct editing."""
import os
from test_tex_cv import source
from playwright.sync_api import expect, sync_playwright

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    context = browser.new_context()
    page = context.new_page()
    pid = None
    try:
        page.goto(f'{web}/profile/new')
        nav = page.get_by_role('navigation', name='Profile setup')
        expect(nav).to_be_visible()
        expect(page.get_by_label('First name', exact=True)).not_to_be_visible()
        page.get_by_role('button', name='Continue', exact=True).click()
        expect(page.get_by_label('Profile name', exact=True)).to_be_focused()
        page.get_by_label('Profile name', exact=True).fill('Onboarding browser test')
        page.get_by_label('Roles you’re interested in', exact=False).fill('Product Owner')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.get_by_label('Skills', exact=False).first.fill('Research, Scrum')
        page.get_by_label('Languages', exact=False).first.fill('German, Spanish')
        page.get_by_label('German proficiency').select_option('B2')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.get_by_label('First name', exact=True).fill('Ana')
        page.get_by_label('Last name', exact=True).fill('López')
        page.get_by_label('Email', exact=True).fill('ana@example.org')
        page.get_by_label('Requires sponsorship', exact=True).select_option('true')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        page.get_by_label('Berufliche Ziele', exact=True).fill('Digitale Produkte entwickeln.')
        page.get_by_role('button', name='Continue', exact=True).click()
        expect(page.get_by_role('heading', name='Review your profile', exact=True)).to_be_visible()
        expect(page.get_by_text('ana@example.org', exact=False)).to_be_visible()
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved')
        pid = int(page.url.split('/profile/')[1].split('/')[0])
        stored = next(p for p in context.request.get(f'{api}/api/v1/profiles').json()['items'] if p['id'] == pid)
        assert stored['desired_roles'] == ['Product Owner']
        assert stored['skills'] == ['Research', 'Scrum']
        assert stored['language_levels']['German'] == 'B2'
        assert stored['application_details']['preferences']['sponsorship_required'] is True
        assert stored['localized_content']['de']['application_personalization']['career_goals'] == 'Digitale Produkte entwickeln.'
        page.goto(f'{web}/profile/{pid}/edit#profile-personalization')
        expect(page.get_by_label('Berufliche Ziele', exact=True)).to_be_visible()
        page.get_by_role('tab', name='English', exact=True).click()
        expect(page.get_by_label('Career goals', exact=True)).to_be_visible()
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        expect(page.get_by_label('Berufliche Ziele', exact=True)).to_have_value('Digitale Produkte entwickeln.')
        nav.get_by_role('button', name='Application details', exact=False).click()
        page.get_by_label('Email', exact=True).fill('broken-email')
        nav.get_by_role('button', name='Review', exact=False).click()
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_label('Email', exact=True)).to_be_visible()
        expect(page.get_by_label('Email', exact=True)).to_be_focused()
        page.get_by_label('Email', exact=True).fill('updated@example.org')
        dialogs = []
        def keep_unsaved_draft(dialog):
            dialogs.append(dialog.type)
            dialog.dismiss()
        page.on('dialog', keep_unsaved_draft)
        page.locator('a.profile-back-link').click()
        assert dialogs == ['beforeunload']
        expect(page.get_by_label('Email', exact=True)).to_have_value('updated@example.org')
        page.remove_listener('dialog', keep_unsaved_draft)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved')
        page.reload()
        expect(page.get_by_text('All changes saved', exact=True)).to_be_visible()
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        page.get_by_role('tab', name='English', exact=True).click()
        expect(page.get_by_text('All changes saved', exact=True)).to_be_visible()
        nav.get_by_role('button', name='Application details', exact=False).click()
        expect(page.get_by_label('Email', exact=True)).to_have_value('updated@example.org')
        # Pasted CV text is a separate transient draft: save must not silently erase it.
        nav.get_by_role('button', name='Experience & skills', exact=False).click()
        page.get_by_text('Suggest skills from my CV text', exact=True).click()
        page.get_by_label('CV text', exact=True).fill('Research, product discovery and project management draft')
        expect(page.get_by_text('Unsaved changes', exact=True)).to_be_visible()
        dialogs.clear()
        page.on('dialog', keep_unsaved_draft)
        page.locator('a.profile-back-link').click()
        assert dialogs == ['beforeunload']
        page.remove_listener('dialog', keep_unsaved_draft)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved')
        expect(page.get_by_label('CV text', exact=True)).to_have_value('Research, product discovery and project management draft')
        page.get_by_role('button', name='Discard pasted text', exact=True).click()
        expect(page.get_by_text('All changes saved', exact=True)).to_be_visible()
        # A failed second upload must keep that file queued and retain the first CV.
        nav.get_by_role('button', name='CVs & writing', exact=False).click()
        page.get_by_label('English base CV', exact=True).set_input_files({'name': 'english.tex', 'mimeType': 'text/plain', 'buffer': source().encode()})
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        page.get_by_label('German base CV', exact=True).set_input_files({'name': 'deutsch.tex', 'mimeType': 'text/plain', 'buffer': source('de').encode()})
        def fail_german_upload(route):
            route.fulfill(status=503, json={'detail': 'Upload interrupted. Try again.'})
        page.route('**/api/v1/profiles/*/cv?*language=de', fail_german_upload)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('Upload interrupted')
        expect(page.get_by_text('deutsch.tex · ready to save', exact=True)).to_be_visible()
        page.unroute('**/api/v1/profiles/*/cv?*language=de', fail_german_upload)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved')
        stored = next(p for p in context.request.get(f'{api}/api/v1/profiles').json()['items'] if p['id'] == pid)
        assert set(stored['cv']['languages']) == {'en', 'de'}
        # Failed profile writes leave the draft available for retry.
        nav.get_by_role('button', name='Application details', exact=False).click()
        page.get_by_label('Email', exact=True).fill('retry@example.org')
        def fail_profile_save(route):
            route.fulfill(status=503, json={'detail': 'Save interrupted. Try again.'})
        page.route(f'{api}/api/v1/profiles/{pid}', fail_profile_save)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('Save interrupted')
        expect(page.get_by_label('Email', exact=True)).to_have_value('retry@example.org')
        page.unroute(f'{api}/api/v1/profiles/{pid}', fail_profile_save)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # Application return links must also retain unprocessed pasted CV text.
        page.goto(f'{web}/profile/{pid}/edit?return_to=%2Fjobs%2F1%2Fapply#profile-experience')
        page.get_by_text('Suggest skills from my CV text', exact=True).click()
        page.get_by_label('CV text', exact=True).fill('CV text to process before returning to the application')
        page.get_by_role('button', name='Save & continue', exact=True).click()
        expect(page.get_by_label('CV text', exact=True)).to_have_value('CV text to process before returning to the application')
        assert f'/profile/{pid}/edit' in page.url
        page.get_by_role('button', name='Discard pasted text', exact=True).click()
        page.get_by_role('button', name='Save & continue', exact=True).click()
        page.wait_for_url(f'**/jobs/1/apply?profile_id={pid}')
        # Loading errors must not expose a blank editor that could overwrite the profile.
        def fail_load(route):
            route.fulfill(status=503, json={'detail': 'Profiles unavailable'})
        page.route(f'{api}/api/v1/profiles', fail_load)
        page.goto(f'{web}/profile/{pid}/edit')
        expect(page.get_by_role('alert')).to_contain_text('Profiles unavailable')
        expect(page.get_by_role('navigation', name='Profile setup')).to_have_count(0)
        page.unroute(f'{api}/api/v1/profiles', fail_load)
        page.route(f'{api}/api/v1/profiles', lambda route: route.fulfill(json={'items': []}))
        page.goto(f'{web}/profile')
        expect(page.get_by_role('link', name='Set up my profile', exact=True)).to_have_attribute('href', '/profile/new')
        print('Guided setup, bilingual persistence, validation, retry recovery, draft protection, return links, empty state and mobile layout passed.')
    finally:
        if pid:
            context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
