"""Language tab edits survive switching, saving and reloading; shared facts remain shared."""
import os
from playwright.sync_api import expect, sync_playwright

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    context = browser.new_context()
    saved = context.request.post(f'{api}/api/v1/profiles', data={'name': 'Bilingual browser test', 'summary': 'Existing English summary', 'application_details': {'email': 'alex@example.org', 'preferences': {'salary_amount': '65000', 'notice_period': 'Three months'}}}).json()
    pid = saved['id']
    try:
        page = context.new_page()
        page.goto(f'{web}/profile/{pid}/edit')
        expect(page.get_by_role('tab', name='English', exact=True)).to_have_attribute('aria-selected', 'true')
        expect(page.get_by_label('Profile summary', exact=True)).to_have_value('Existing English summary')
        page.get_by_label('Career goals', exact=True).fill('Build research tools.')
        page.get_by_label('Skills', exact=False).first.fill('Python, Research')
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        expect(page.get_by_label('Profilzusammenfassung', exact=True)).to_have_value('')
        expect(page.get_by_label('Kündigungsfrist', exact=True)).to_have_value('')
        page.get_by_label('Profilzusammenfassung', exact=True).fill('Forschung und Entwicklung')
        page.get_by_label('Berufliche Ziele', exact=True).fill('Forschungswerkzeuge entwickeln.')
        page.get_by_label('Kündigungsfrist', exact=True).fill('Drei Monate')
        page.get_by_label('Fähigkeiten', exact=False).first.fill('Python, Forschung')
        page.get_by_label('Email', exact=True).fill('shared@example.org')
        page.get_by_role('tab', name='English', exact=True).click()
        expect(page.get_by_label('Career goals', exact=True)).to_have_value('Build research tools.')
        expect(page.get_by_label('Notice period', exact=True)).to_have_value('Three months')
        expect(page.get_by_label('Email', exact=True)).to_have_value('shared@example.org')
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        expect(page.get_by_label('Berufliche Ziele', exact=True)).to_have_value('Forschungswerkzeuge entwickeln.')
        # Hold the save response to exercise tab switching during a pending request.
        def delay_save(route):
            response = route.fetch()
            page.wait_for_timeout(500)
            expect(page.get_by_role('tab', name='English', exact=True)).to_be_disabled()
            expect(page.get_by_role('tab', name='Deutsch', exact=True)).to_be_disabled()
            route.fulfill(response=response)
        page.route(f'{api}/api/v1/profiles/{pid}', delay_save)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved.')
        page.reload()
        expect(page.get_by_label('Profile summary', exact=True)).to_have_value('Existing English summary')
        expect(page.get_by_label('Career goals', exact=True)).to_have_value('Build research tools.')
        page.get_by_role('tab', name='Deutsch', exact=True).click()
        expect(page.get_by_label('Profilzusammenfassung', exact=True)).to_have_value('Forschung und Entwicklung')
        expect(page.get_by_label('Berufliche Ziele', exact=True)).to_have_value('Forschungswerkzeuge entwickeln.')
        expect(page.get_by_label('Kündigungsfrist', exact=True)).to_have_value('Drei Monate')
        expect(page.get_by_label('Email', exact=True)).to_have_value('shared@example.org')
        expect(page.get_by_role('tablist').get_by_label('Email', exact=True)).to_have_count(0)
        stored = next(p for p in context.request.get(f'{api}/api/v1/profiles').json()['items'] if p['id'] == pid)
        assert stored['summary'] == 'Existing English summary'
        assert stored['application_details']['preferences']['salary_amount'] == '65000'
        assert stored['localized_content']['de']['skills'] == ['Python', 'Forschung']
        assert stored['localized_content']['en']['skills'] == ['Python', 'Research']
        assert stored['application_details']['preferences']['notice_period'] == 'Three months'
        page.screenshot(path='/tmp/bilingual-profile.png', full_page=True)
        print('Unsaved language switches, shared contacts, save from German tab and both reloads passed.')
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
