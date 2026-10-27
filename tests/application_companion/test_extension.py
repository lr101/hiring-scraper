"""Load the real MV3 extension in Chromium; no live applicant data or requests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent
EXTENSION = ROOT.parents[1] / "extensions" / "application-companion"
DEMO = "http://127.0.0.1:43199/fixture.html"
GREENHOUSE = "https://job-boards.greenhouse.io/extension-poc/jobs/123"
GREENHOUSE_HTML = """<!doctype html><title>Offline Greenhouse-shaped fixture</title>
<form><label>First Name<input id=first_name></label><label>Last Name<input id=last_name></label>
<label>Email<input id=email></label><label>Phone<input id=phone></label>
<label>Authorization<input id=question_authorization></label><label><input id=consent type=checkbox>Consent</label>
<input id=resume type=file><button type=submit>Submit application</button></form>
<script>window.draft={submissions:0}; document.querySelectorAll('input').forEach(e=>e.oninput=()=>window.draft[e.id]=e.value);
document.querySelector('form').onsubmit=e=>{e.preventDefault();window.draft.submissions++};</script>"""


class ExtensionHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("RADIUS_EXTENSION_BROWSER_TESTS") != "1":
            raise unittest.SkipTest("Set RADIUS_EXTENSION_BROWSER_TESTS=1 to run Chrome extension integration tests")
        cls.playwright = sync_playwright().start()

    @classmethod
    def tearDownClass(cls):
        cls.playwright.stop()

    def setUp(self):
        self.directory = TemporaryDirectory()
        extension_path=getattr(self,"extension_path",EXTENSION)
        self.context = self.playwright.chromium.launch_persistent_context(
            self.directory.name, channel="chromium", headless=True, service_workers=getattr(self, "service_workers", "block"),
            args=[f"--disable-extensions-except={extension_path}", f"--load-extension={extension_path}",
                  "--enable-unsafe-extension-debugging"],
        )
        self.context.set_default_timeout(5000)
        if os.environ.get("RADIUS_BROWSER_TRACE_DIR"):
            self.context.tracing.start(screenshots=True, snapshots=True)
        self.errors = []
        self.external_requests = []
        self.context.on("page", lambda page: page.on("pageerror", lambda error: self.errors.append(str(error))))
        self.context.route("**/*", self.route)
        self.worker = self.context.service_workers[0] if self.context.service_workers else self.context.wait_for_event("serviceworker", timeout=30000)
        self.extension_id = self.worker.url.split("/")[2]
        self.cdp = self.context.browser.new_browser_cdp_session()
        self.applicant = json.loads((ROOT / "profile.example.json").read_text())
        self.page = self.context.pages[0]

    def route(self, route):
        url = route.request.url
        if url.startswith(DEMO):
            route.fulfill(content_type="text/html", body=(ROOT / "fixture.html").read_text())
        elif url == GREENHOUSE:
            route.fulfill(content_type="text/html", body=GREENHOUSE_HTML)
        elif url.startswith("https://unsupported.example/"):
            route.fulfill(content_type="text/html", body=(ROOT / "fixture.html").read_text())
        elif url.startswith("chrome-extension:"):
            route.continue_()
        elif url.endswith("/favicon.ico"):
            route.fulfill(status=204)
        else:
            self.external_requests.append(url)
            route.abort()

    def tearDown(self):
        trace_dir = os.environ.get("RADIUS_BROWSER_TRACE_DIR")
        if trace_dir:
            result = self._outcome.result
            failed = any(test is self for test, _ in result.failures + result.errors)
            if failed:
                Path(trace_dir).mkdir(parents=True, exist_ok=True)
                self.context.tracing.stop(path=str(Path(trace_dir) / f"{self.id()}.zip"))
            else:
                self.context.tracing.stop()
        self.context.close()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external_requests, [])

    def save_example(self):
        self.worker.evaluate("applicant => chrome.storage.local.set({applicant})", self.applicant)

    def load_editor_example(self, options):
        options.get_by_role("button", name="Load fictional example").click()
        expect(options.locator("#status")).to_have_text("Fictional example loaded. Save profile to use it.")

    def open_fixture(self, layout="inline"):
        self.page.goto(DEMO + "?layout=" + layout)

    def popup(self):
        self.page.bring_to_front()
        target = next(t for t in self.cdp.send("Target.getTargets", {"filter": [{"type": "tab"}]})["targetInfos"] if t["url"] == self.page.url)
        # A real browser action grants activeTab. The shipped manifest is unchanged.
        self.cdp.send("Extensions.triggerAction", {"id": self.extension_id, "targetId": target["targetId"]})
        # Drive the same popup document in a tab after the permission grant.
        # Navigating a background tab leaves the application as the active tab.
        popup = self.context.new_page()
        self.page.bring_to_front()
        popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
        expect(popup.locator("#page-name")).not_to_have_text(
            "Checking this page…", timeout=15_000
        )
        return popup

    def fill(self, popup):
        popup.get_by_role("button", name="Fill known details", exact=True).click()
        expect(popup.locator("#status")).not_to_have_text("Filling known details…")

    def report(self):
        return self.worker.evaluate("""async url => {
          const tab=(await chrome.tabs.query({})).find(item=>item.url===url);
          return (await chrome.storage.session.get(`report:${tab.id}`))[`report:${tab.id}`].report;
        }""", self.page.url)


class ExtensionBrowserTests(ExtensionHarness):

    def test_editor_saves_reloads_and_validates_dates(self):
        if __package__:
            from .test_tex_extension import tex_source
        else:
            from test_tex_extension import tex_source
        options = self.context.new_page()
        options.goto(f"chrome-extension://{self.extension_id}/options.html")
        self.load_editor_example(options)
        options.locator('#cv_text').fill(tex_source())
        options.get_by_role("button", name="Save profile", exact=True).click()
        expect(options.locator("#status")).to_have_text("Profile saved in this browser.")
        options.reload()
        expect(options.locator("#first_name")).to_have_value("Alex")
        expect(options.locator('#cv_text')).to_have_value(tex_source())
        expect(options.get_by_role('button', name='Add experience', exact=True)).to_have_count(0)
        options.locator('#cv_text').fill(tex_source().replace('2021', '2021-14'))
        options.get_by_role("button", name="Save profile", exact=True).click()
        expect(options.locator("#status")).to_contain_text("markers")
        saved = options.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant")
        self.assertEqual(saved["experiences"][0]["start"], "2021")

    def test_import_export_and_delete_profile(self):
        options = self.context.new_page()
        options.goto(f"chrome-extension://{self.extension_id}/options.html")
        options.locator('#import-file').set_input_files({"name": "profile.json", "mimeType": "application/json", "buffer": json.dumps(self.applicant).encode()})
        expect(options.locator("#transfer-status")).to_contain_text("Profile imported")
        options.get_by_role("button", name="Save profile", exact=True).click()
        expect(options.locator("#status")).to_have_text("Profile saved in this browser.")
        with options.expect_download() as download:
            options.get_by_role("button", name="Export saved profile").click()
        exported = json.loads(Path(download.value.path()).read_text())
        self.assertEqual([{key:e[key] for key in self.applicant["experiences"][i]} for i,e in enumerate(exported["experiences"])], self.applicant["experiences"])
        self.assertEqual([e["id"] for e in exported["experiences"]],["job-1","job-2","job-3"])
        options.locator('#import-file').set_input_files({"name": "bad.json", "mimeType": "application/json", "buffer": b'{"experiences":[{"company":"incomplete"}]}'})
        expect(options.locator("#transfer-status")).to_have_class("status error")
        expect(options.locator("#first_name")).to_have_value("Alex")
        options.once("dialog", lambda dialog: dialog.accept())
        options.get_by_role("button", name="Delete saved profile", exact=True).click()
        expect(options.locator("#status")).to_have_text("Saved profile deleted.")
        self.assertIsNone(options.evaluate("async () => (await chrome.storage.local.get('applicant')).applicant ?? null"))

    def test_no_profile_disables_fill(self):
        self.open_fixture()
        popup = self.popup()
        expect(popup.get_by_role("button", name="Fill known details", exact=True)).to_be_disabled()
        self.assertEqual(self.page.evaluate("window.applicationDraft.experiences.length"), 0)




    def test_generic_origin_does_not_get_local_experience_automation(self):
        self.save_example()
        self.page.goto("https://unsupported.example/fixture.html")
        popup = self.popup()
        expect(popup.locator("#page-name")).to_have_text("Application form (best effort)")
        self.fill(popup)
        self.assertEqual(self.page.evaluate("window.applicationDraft.first_name"), "Alex")
        self.assertEqual(self.page.evaluate("window.applicationDraft.experiences.length"),0)

    def test_greenhouse_contacts_on_offline_hosted_form_shape(self):
        self.applicant["phone"] = "+49123456789"
        self.save_example(); self.page.goto(GREENHOUSE)
        popup = self.popup(); self.fill(popup)
        self.assertEqual(self.report()["adapter"], "greenhouse-contact")
        self.assertEqual(self.report()["status"], "partial")
        draft = self.page.evaluate("window.draft")
        self.assertEqual(draft["first_name"], "Alex")
        self.assertEqual(draft["phone"], "+49123456789")
        self.assertEqual(draft["submissions"], 0)
        self.assertEqual(self.page.locator('#question_authorization').input_value(), "")
        self.assertFalse(self.page.locator('#consent').is_checked())
        self.fill(popup)
        self.assertEqual(self.report()["filled"], 0)



if __name__ == "__main__":
    unittest.main()

class AuthenticationSafetyTests(ExtensionHarness):
    def test_greenhouse_adapter_refuses_password_and_otp_pages(self):
        self.save_example()
        for credential in ['<input type=password>', '<input autocomplete=one-time-code>']:
            self.context.route(GREENHOUSE, lambda route, request, html=GREENHOUSE_HTML + credential: route.fulfill(content_type='text/html', body=html))
            self.page.goto(GREENHOUSE)
            popup = self.popup()
            self.assertTrue(popup.locator('#fill').is_disabled(), 'Every adapter must refuse authentication forms')
            self.assertEqual(self.page.locator('#first_name').input_value(), '')
            self.assertEqual(self.page.locator('#email').input_value(), '')
            popup.close()
