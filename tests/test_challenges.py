import unittest

from hiring_scraper.challenges import is_bot_challenge


class ChallengeClassificationTests(unittest.TestCase):
    def test_explicit_provider_headers_and_interstitial_copy_are_challenges(self):
        self.assertTrue(is_bot_challenge(b"", "challenge"))
        self.assertTrue(is_bot_challenge(b"<div class='cf-chl-widget'>"))
        self.assertTrue(is_bot_challenge("Please complete the CAPTCHA to continue."))
        self.assertTrue(is_bot_challenge("This captcha is required to access the page."))

    def test_captcha_libraries_and_job_text_are_not_challenges(self):
        page = """<html><script src="https://www.google.com/recaptcha/api.js"></script>
        <div class="g-recaptcha" data-sitekey="public-key"></div>
        <p>Applicants may be asked to complete a coding challenge.</p></html>"""
        self.assertFalse(is_bot_challenge(page))
        self.assertFalse(is_bot_challenge(b"Our application form uses hCaptcha."))


if __name__ == "__main__":
    unittest.main()
