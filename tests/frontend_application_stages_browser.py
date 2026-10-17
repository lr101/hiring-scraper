"""Parallel Journey stages support click transitions, plugin writes and reloads."""
import os
import re
from playwright.sync_api import sync_playwright, expect

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    profile = context.request.post(f'{api}/api/v1/profiles', data={'name': 'Parallel stages test'}).json()
    pid = profile['id']
    initial_sent = context.request.get(f'{api}/api/v1/application-momentum').json()['applied_this_week']
    try:
        page = context.new_page()
        page.goto(f'{web}/applications?profile_id={pid}&statuses=new')
        stretch_filter = page.get_by_role('button', name=re.compile(r'^In GOD's hand, .* roles$'))
        automated_filter = page.get_by_role('button', name=re.compile(r'^Automated application, .* roles$'))
        expect(stretch_filter).to_be_visible(timeout=15000)
        expect(automated_filter).to_be_visible()
        # Both alternatives occupy the same column as their primary stage.
        for primary, alternative in [('Interested', stretch_filter), ('Waiting for reply', automated_filter)]:
            first = page.get_by_role('button', name=re.compile(f'^{primary}, .* roles$')).bounding_box()
            second = alternative.bounding_box()
            assert abs(first['x'] - second['x']) < 1 and second['y'] > first['y'], 'Alternatives share a column'
        card = page.locator('.application-job-card').first
        expect(card).to_be_visible(timeout=90000)
        job_id = int(card.locator('.application-job-title').get_attribute('href').split('/jobs/')[1].split('?')[0])
        card.get_by_role('button', name='In GOD's hand', exact=True).click()
        expect(stretch_filter).to_have_accessible_name('In GOD's hand, 1 roles')
        stretch_filter.click()
        expect(page.locator('.application-job-card')).to_have_count(1)
        expect(page.locator('.application-state-badge')).to_have_text('In GOD's hand')
        page.reload()
        expect(page.locator('.application-state-badge')).to_have_text('In GOD's hand', timeout=25000)
        expect(page.locator('.application-job-card').get_by_role('button', name='Automated application', exact=True)).to_have_count(0)
        # The sent-autofill stage is written by the plugin, not a manual action.
        saved = context.request.put(f'{api}/api/v1/profiles/{pid}/applications/{job_id}', data={'status':'automated_application'})
        assert saved.ok, saved.text()
        page.reload()
        expect(automated_filter).to_have_accessible_name('Automated application, 1 roles')
        expect(page.get_by_role('progressbar', name='Weekly application pace')).to_have_attribute('aria-valuenow', str(min(10, initial_sent + 1)))
        automated_filter.click()
        expect(page.locator('.application-state-badge')).to_have_text('Automated application')
        page.get_by_role('button', name='Undo to In GOD's hand', exact=False).click()
        expect(stretch_filter).to_have_accessible_name('In GOD's hand, 1 roles')
        # Simulate the browser plugin persisting its result through the public API.
        saved = context.request.put(f'{api}/api/v1/profiles/{pid}/applications/{job_id}', data={'status':'automated_application'})
        assert saved.ok, saved.text()
        page.reload()
        expect(page.locator('.application-state-badge')).to_have_text('Automated application', timeout=25000)
        page.locator('.application-job-card').get_by_role('button', name='Log interview', exact=True).click()
        interview_filter = page.get_by_role('button', name='Interview, 1 roles', exact=True)
        expect(interview_filter).to_be_visible()
        interview_filter.click()
        expect(page.locator('.application-state-badge')).to_have_text('Interview')
        # Interested offers the same stretch-role destination as New.
        context.request.put(f'{api}/api/v1/profiles/{pid}/applications/{job_id}', data={'status':'open'})
        page.goto(f'{web}/applications?profile_id={pid}&statuses=open')
        page.locator('.application-job-card').get_by_role('button', name='In GOD's hand', exact=True).click()
        expect(stretch_filter).to_have_accessible_name('In GOD's hand, 1 roles')
        stretch_filter.click()
        saved = context.request.put(f'{api}/api/v1/profiles/{pid}/applications/{job_id}', data={'status':'automated_application'})
        assert saved.ok, saved.text()
        page.reload()
        expect(automated_filter).to_have_accessible_name('Automated application, 1 roles')
        automated_filter.click()
        expect(page.locator('.application-state-badge')).to_have_text('Automated application')
        for theme in ['light', 'dark']:
            page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
            for width in [320, 390, 768, 1440]:
                page.set_viewport_size({'width':width,'height':1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (theme,width)
                assert automated_filter.evaluate('(el) => el.scrollWidth <= el.clientWidth'), 'Stage label must fit'
        print('Parallel stage placement, New/Interested transitions, plugin API write, undo, reload, outcomes and mobile layout passed')
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
