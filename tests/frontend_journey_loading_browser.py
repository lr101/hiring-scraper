"""Measure first/repeated Journey loads and profile wording edits on an isolated preview."""
import json
import os
from time import perf_counter
from playwright.sync_api import expect, sync_playwright

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
api = os.environ['APPLICATION_TEST_API_URL'].rstrip('/')
max_reload_seconds = float(os.environ.get('APPLICATION_TEST_MAX_RELOAD_SECONDS', '5'))
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    context = browser.new_context()
    profile = context.request.post(f'{api}/api/v1/profiles', data={
        'name': 'Journey loading regression', 'skills': ['Python'],
        'matching_defaults': {'min_match_score': 0},
    }).json()
    pid = profile['id']
    try:
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        timings = {}
        conflict_requests = []
        def interrupt_first_read(route):
            conflict_requests.append(route.request.url)
            if len(conflict_requests) == 1:
                route.fulfill(status=409, content_type='application/json', body=json.dumps({
                    'detail': 'Profile or job inventory changed during search; retry the request',
                }))
            else:
                route.continue_()
        for label in ['first_load', 'reload_1', 'reload_2', 'reload_3', 'after_writing_edit', 'after_inventory_conflict']:
            if label == 'after_writing_edit':
                payload = {key: value for key, value in profile.items() if key not in {'id', 'cv', 'updated_at'}}
                payload.update(custom_instructions='Keep the cover letter concise.',
                    application_details={'email': 'journey-test@example.org'},
                    application_personalization={'career_goals': 'Build reliable research tools.'},
                    localized_content={'de': {'summary': 'Forschungswerkzeuge entwickeln.'}})
                updated = context.request.put(f'{api}/api/v1/profiles/{pid}', data=payload)
                assert updated.ok, updated.text()
            if label == 'after_inventory_conflict':
                page.route(f'{api}/api/v1/applications?*', interrupt_first_read)
            started = perf_counter()
            if label == 'first_load':
                page.goto(f'{web}/?profile_ids={pid}&statuses=new')
            else:
                page.reload()
            expect(page.locator('.application-job-card').first).to_be_visible(timeout=90000)
            timings[label] = round(perf_counter() - started, 3)
            if label != 'first_load':
                assert timings[label] < max_reload_seconds, f'{label} took {timings[label]}s (budget: {max_reload_seconds}s)'
            if label == 'after_inventory_conflict':
                assert len(conflict_requests) == 2, conflict_requests
                page.unroute(f'{api}/api/v1/applications?*', interrupt_first_read)
        # Nonempty pages and working stage switches are the user-facing contract;
        # the backend regression separately checks that wording edits do not rebuild.
        expect(page.locator('.application-job-card')).to_have_count(40)
        page.locator('[data-journey-stage="open"]').click()
        expect(page.locator('[data-journey-stage="open"]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('.loading-state')).to_have_count(0, timeout=15000)
        page.locator('[data-journey-stage="new"]').click()
        expect(page.locator('.application-job-card')).to_have_count(40)
        assert not errors, errors
        print(json.dumps({'journey_seconds': timings, 'cards': 40, 'browser_errors': 0}))
    finally:
        context.request.delete(f'{api}/api/v1/profiles/{pid}')
        browser.close()
