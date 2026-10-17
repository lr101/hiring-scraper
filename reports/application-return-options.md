# A simpler ChatGPT application return

Evaluated 2026-10-16 against the user’s mobile screenshot and the current Radius code.

The screenshot exposes a concrete failure: four custom markers become loose text around a writing block. There is no single obvious result to copy. More explanatory labels alone would not repair that contract.

The implemented default is now **one compressed Return to Radius link**, with the JSON package kept internal. This supersedes the earlier single-code-block baseline evaluated below. A file import is the fallback; manual JSON paste is hidden under a disclosure. The Python zlib/base64url recipe is executed in tests, and its links open the original-template editor. A normal prompt cannot guarantee ChatGPT tool availability or compliance. A fixed short token URL would need a connected authenticated Action/MCP that stores the generated package on Radius.

| Option | Decision | What was actually evaluated or tested |
| --- | --- | --- |
| One JSON code block with Copy | Manual fallback, implemented | Raw JSON and whole answers containing one fenced JSON block import atomically. Pasting opens documents immediately. Unicode, invalid output and original DOCX/PDF export are covered. |
| Generated return link | Default, implemented | The actual Python recipe from the prompt generates a zlib-compressed base64url fragment URL. A browser external-link click opens the right application and inserts documents; the fragment is removed. Job/profile/CV identity is checked. Large URLs were exercised; the prompt omits links over 24,000 characters. |
| Download application-package.json | Fallback, implemented | Browser file selection imports the same validated object. Useful when mobile text selection or clipboard permissions fail. ChatGPT must offer the file; availability is not guaranteed. |
| Canvas or a writing block | Optional drafting surface, not the return contract | The user’s screenshot already demonstrates a writing block Copy affordance, but copying the letter alone omits CV edits and answers. A single code canvas could hold JSON, but the app cannot force that UI or depend on a Canvas-to-Radius bridge. Native ChatGPT Canvas UI was not tested. |
| ChatGPT Action or MCP plugin | Stronger future automation | Official tools can send structured results to an authenticated app endpoint, eliminating manual returns. Requires a connected custom GPT/plugin, authenticated scoped write tools and account testing. No live integration was configured or claimed. |
| Sign in with ChatGPT plan usage | Best candidate for a future direct customization button | Current official documentation supports eligible open-source/local apps using a user-authorized ChatGPT plan for Responses requests. It does not expose ChatGPT conversations or other account context. A remote public app needs eligibility review; the documented self-hosted VM path involves local OAuth and protected credential transfer. Not implemented or live-tested here. |
| Browser extension/bookmarklet | Possible desktop experiment, not the mobile default | An extension could read the ChatGPT result and deliver it to Radius, but requires installation and tightly scoped browser permissions. Ordinary app code cannot read another origin’s DOM. No extension was installed. |
| PWA share target | Promising mobile follow-up | Sharing a downloaded JSON file to an installed Radius PWA could reduce the import steps. It requires an installed share-target handler and OS/browser testing. This headless browser has no navigator.share; phone behavior remains untested. |
| QR code / compressed magic link | Compression implemented; QR remains an idea | Compressed magic links now work in the browser. QR could transfer packages between devices, but long documents create dense codes; no QR flow was added. |
| Generated HTML that posts back | Rejected as default | A local generated page could contain a return button or form, but executing arbitrary generated HTML adds unnecessary script exposure. JSON import provides the same data transfer without executing model-written code. |
| Redirect directly to employer and fill form | Separate integration required | The implemented return goes to Radius’s application page. Filling an arbitrary employer’s website requires a browser extension or authorized browser automation; a return link cannot grant cross-origin access. No employer was contacted and no application was submitted. |

## Practical behavior

1. Attach the original CV to a profile once; DOCX retains the editable template.
2. Start an application and use **Copy brief & open ChatGPT**.
3. In ChatGPT paste and send. The prompt requests one package, never split writing blocks.
4. Click **Return to Radius with my documents**. If code tools cannot create a valid link, import the downloaded package file; manual JSON paste remains the last fallback.
5. Download PDF/DOCX; editing is optional. The original upload is unchanged.

Drafts are saved for this job/profile in this browser. New tabs and refreshes recover them. Clear removes the browser copy. Expired records are pruned on access and active drafts have an expiry check; browser code cannot delete storage while the browser is closed. Stored drafts are not encrypted and are not shared between devices.

## Evidence and limits

- `tests/frontend_application_handoff.mjs`: one-package parsing, whole-answer extraction, context binding, Unicode links, malformed URLs, link size fallback and escaped document rendering.
- `tests/frontend_application_return_browser.py`: fragment navigation and actual external link click; HTTP URL privacy; unknown/stale returns preserve edits; reload/new-tab drafts; direct paste and file import; execution of the exact Python recipe embedded in the app’s prompt; mobile widths; clear and draft expiry.
- `tests/frontend_application_handoff_browser.py`: full profile upload → reuse → ChatGPT handoff → template PDF/DOCX exports, invalid replies, clipboard denial and popup blocking. ChatGPT is intercepted with a local test response.
- `tests/test_cv_template.py`: unchanged template package parts, formatting, page geometry, text coordinates/fonts and original header pixel comparison.

These tests use synthetic CVs and representative outputs. The browser mechanics and recipe are tested; a real model can still ignore instructions, truncate content or decline to generate a link. Direct access to ChatGPT from the test browser returned **HTTP 403**, so no logged-in account, Canvas session, generated-file availability or model-produced return-link success rate was verified. File import and manual JSON paste remain available when no link is generated. T3’s collaborative browser also reported a host AppArmor sandbox restriction; the repository Playwright workflow was used for browser verification.

## Official sources

- [GPT Actions](https://developers.openai.com/api/docs/actions/introduction): REST tools can retrieve data and act in an external application using configured authentication.
- [Add UI to an MCP server](https://developers.openai.com/plugins/build/chatgpt-ui): structured tools can return an interactive UI in ChatGPT and call application tools through the host bridge.
- [Sign in with ChatGPT](https://developers.openai.com/siwc/quickstart) and [open-source plan usage](https://developers.openai.com/siwc/token-sharing-open-source): eligible apps can request user-authorized plan usage; identity and inference scopes do not provide conversation history.
- [Self-hosted VMs](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms): complete OAuth locally, preserve the VM host ID and transfer protected credentials securely. This is not a generic public-web callback.
- [Current preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations): request requirements and unsupported hosted tools must be respected in a direct integration.
- [Use ChatGPT](https://learn.chatgpt.com/docs/use-chatgpt): file creation/download and connected tools depend on account/platform availability.

Canvas and shortcut decisions above are engineering assessments, not promises of platform support.
