# Two-page CV formatting trial

Mara Neumann is a fictional project manager with eleven years of experience. The sample includes three employers, dated roles, project budgets and results, a master's and bachelor's degree, qualifications, tools, languages, volunteering, a hyperlink, real bullet numbering, mixed text emphasis, date/content tables, headers, footers and page-number fields. The PDF/DOCX documents contain finished CV text without applicant instructions or placeholders.

- [Original CV — DOCX](mara-neumann-original.docx)
- [Original CV — PDF](mara-neumann-original.pdf)
- [Tailored CV — DOCX](mara-neumann-tailored.docx)
- [Tailored CV — PDF](mara-neumann-tailored.pdf)
- [Sample application package](mara-neumann-application-package.json)
- [Machine-readable results](results.json)

| Input | Pages | All paragraph text retained | Words outside page bounds |
| --- | ---: | --- | ---: |
| Original | 2 | Yes | 0 |
| Concise tailoring | 2 | Yes | 0 |
| Deliberate expansion | 3 | Yes | 0 |
| Heavy expansion | 5 | Yes | 0 |

Both original/tailored header and footer regions have identical rendered pixels on **both** pages. DOCX styles, numbering, relationships, headers, footers and other package parts remain unchanged. Section, paragraph, run and table formatting are checked too. The normal tailoring respects every paragraph-length budget. The expansion cases intentionally exceed those budgets, demonstrating reflow rather than promising arbitrary longer content will remain two pages. All original/edited paragraph text remains extractable and PDF word boxes remain within the page in those cases; the normal two-page documents were inspected visually.

The prompt now requests finished personalized output using personal context already available in the ChatGPT conversation/project/enabled memory. It does not require another approval or generate confirmation placeholders. Unknown facts are omitted rather than fabricated. Returned documents show download buttons before optional text editing. Obvious unfinished drafting text is rejected on import and blocked from export, preserving any existing completed documents.

The sample tailoring is generated for this test, not obtained from a logged-in personal ChatGPT account. The app can instruct the user's ChatGPT conversation to use available context; it does not itself access private ChatGPT memory. Previous environment access to ChatGPT returned HTTP 403.

Reproduce the artifact trial:

```sh
.venv/bin/python experiments/cv_formatting_trial.py
.venv/bin/python -m unittest discover -s tests -p 'test_cv_realistic.py' -v
```

`tests/frontend_application_ready_browser.py` additionally uploads this CV, imports a tailored package via a return link, downloads a two-page PDF and template-preserving DOCX without opening the editor or confirming facts, checks malformed/unfinished input cannot replace finished documents, and checks unfinished manual edits cannot export. The regular handoff and return browser suites cover clipboard failures, other import paths, draft expiry and mobile layouts.

PNGs are rendered one page at a time: the local Poppler build's multi-page raster path omitted repeated header/footer glyphs in later images, while PDF text/crops and independent page rendering were correct. The supplied PNGs use independent page rendering.
