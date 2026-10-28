"""The extension must not use generated answers before explicit review."""
from playwright.sync_api import expect
from .test_application_journey import JourneyBrowserTests, LISTING, ATS

class ReviewGateTests(JourneyBrowserTests):
    def test_generated_package_requires_review_before_filling(self):
        self.seed(True)
        self.page.goto(LISTING)
        popup = self.popup()
        with self.context.expect_page() as opened:
            self.page.get_by_role('link', name='Apply now', exact=True).click()
        self.page = opened.value
        self.page.wait_for_url(ATS)
        popup.close()
        popup = self.popup()
        popup.locator('#fill').click()
        expect(popup.locator('#status')).to_contain_text('review', ignore_case=True)
        assert self.page.locator('#letter').input_value() == ''
        popup.get_by_role('button', name='I reviewed these documents', exact=True).click()
        popup.locator('#fill').click()
        expect(self.page.locator('#letter')).to_have_value('Prepared letter for this role.')
        assert self.page.evaluate('window.submits') == 0

    def test_popup_shows_running_preparation_status(self):
        self.seed(False)
        self.worker.evaluate("""() => {const previous = globalThis.fetch; globalThis.fetch = (input, options) => String(input).includes('/preparation-job?') ? Promise.resolve(new Response(JSON.stringify({job:{state:'running',stage:'drafting'}}), {headers:{'Content-Type':'application/json'}})) : previous(input, options)}""")
        self.page.goto(LISTING)
        popup = self.popup()
        expect(popup.locator('#job-entries')).to_contain_text('Drafting documents')

    def test_review_confirmation_rejects_changed_package(self):
        self.seed(True)
        self.page.goto(LISTING)
        popup = self.popup()
        confirm = popup.get_by_role('button', name='I reviewed these documents', exact=True)
        expect(confirm).to_be_visible()
        self.worker.evaluate("""async () => { const {radiusVault:v} = await chrome.storage.local.get('radiusVault'); v.packages['1574:1'].package.cover_letter = 'Changed after display'; await chrome.storage.local.set({radiusVault:v}); }""")
        confirm.click()
        expect(popup.locator('#status')).to_contain_text('changed')
        reviewed = self.worker.evaluate("async () => (await chrome.storage.local.get('radiusVault')).radiusVault.packages['1574:1'].reviewed")
        self.assertNotEqual(reviewed, True)
