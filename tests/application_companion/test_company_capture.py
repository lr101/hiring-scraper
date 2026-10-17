"""Exercise the actual MV3 popup's company capture and per-job profile picker."""
from playwright.sync_api import expect

from .test_extension import ExtensionHarness


class CompanyCaptureBrowserTests(ExtensionHarness):
    def setUp(self):
        super().setUp()
        self.context.route('https://acme.example/**', lambda route: route.fulfill(
            content_type='text/html', body='<title>Acme careers</title><h1>Work at Acme</h1>'))
        self.worker.evaluate('''async () => {
          await chrome.storage.local.set({radiusVault: {
            settings: {origin: 'https://radius.example', api_origin: 'https://radius.example', profile_id: 1},
            profiles: [{id: 1, name: 'Developer'}, {id: 2, name: 'Designer'}],
            jobs: [], bindings: [], packages: {}, synced_at: Date.now()
          }});
          globalThis.captureRequests = [];
          const fetchOriginal = globalThis.fetch;
          globalThis.fetch = async (input, options) => {
            const url = String(input);
            if (!url.startsWith('https://radius.example/api/v1/')) return fetchOriginal(input, options);
            const body = JSON.parse(options.body);
            captureRequests.push({url, body});
            const result = url.endsWith('/feeds')
              ? {company_name: 'Acme', company_created: true, already_tracked: false}
              : {job: {id: 42, title: body.title, company_name: 'Acme', url: body.url, is_active: true},
                 profile_name: 'Designer', status: 'new', already_added: false};
            return new Response(JSON.stringify(result), {status: 201, headers: {'Content-Type': 'application/json'}});
          };
        }''')
        self.page.goto('https://acme.example/careers')

    def test_default_action_tracks_current_company_without_job_or_profile_fields(self):
        popup = self.popup()
        expect(popup.locator('#add-kind')).to_have_value('feed')
        expect(popup.locator('#add-feed-url')).to_be_visible()
        expect(popup.locator('#add-job-profile')).to_be_hidden()
        expect(popup.locator('#add-feed-url')).to_have_value('https://acme.example/careers')
        popup.get_by_role('button', name='Track company', exact=True).click()
        expect(popup.locator('#add-status')).to_have_text('Now tracking Acme. Its first job scan is queued.')
        request = self.worker.evaluate('captureRequests[0]')
        self.assertTrue(request['body']['track_company'])
        self.assertEqual(request['body']['provider'], 'auto')
        self.assertNotIn('profile_id', request['body'])

    def test_single_job_uses_chosen_profile_and_explicit_employer_website(self):
        popup = self.popup()
        popup.locator('#add-kind').select_option('job')
        popup.locator('#add-company-website').fill('https://acme.example/')
        popup.locator('#add-job-title').fill('Product designer')
        popup.locator('#add-job-profile').select_option('2')
        expect(popup.locator('#add-feed-url')).to_be_hidden()
        popup.get_by_role('button', name='Add job to profile', exact=True).click()
        expect(popup.locator('#add-status')).to_contain_text('to Designer as a new application')
        request = self.worker.evaluate('captureRequests[0]')
        self.assertEqual(request['body']['profile_id'], 2)
        self.assertEqual(request['body']['website_url'], 'https://acme.example/')
        self.assertNotIn('track_company', request['body'])
