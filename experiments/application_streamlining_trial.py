"""Offline feasibility probes; no model calls, employer requests, or applications.

Run with the project's Python environment:
  python -m experiments.application_streamlining_trial
  python -m experiments.application_streamlining_trial --browser

Browser mode requires the project's Playwright dependency and installed Chromium.
These deliberately small synthetic cases are diagnostics, not accuracy benchmarks.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from hiring_scraper import matching

ROOT = Path(__file__).resolve().parents[1]


def matching_trial():
    profile_path = ROOT / 'fixtures/cv_profile.json'
    profile = json.loads(profile_path.read_text())
    duties = ('<h3>Responsibilities</h3><p>Coordinate product development and onboarding '
              'with international teams, document progress and maintain plans. '
              'Organize planning sessions, maintain the delivery calendar, prepare team updates '
              'and follow up on agreed actions. Work with colleagues across several offices '
              'to support smooth delivery and keep project documentation current.</p>'
              '<p>Project coordination, stakeholder management and process improvement.</p>')
    cases = [
        ('base', 'Project coordinator', '', 'hybrid'),
        ('tenure_2', 'Project coordinator', 'At least 2 years of professional experience required.', 'hybrid'),
        ('tenure_5', 'Project coordinator', 'At least 5 years of professional experience required.', 'hybrid'),
        ('language_c1', 'Project coordinator', 'German C1 required.', 'hybrid'),
        ('language_optional', 'Project coordinator', 'German C1 is optional.', 'hybrid'),
        ('academic_cad', 'CAD designer', 'At least 3 years of professional experience with SolidWorks required.', 'hybrid'),
        ('unquantified_cad', 'CAD designer', 'Professional industrial SolidWorks experience is required.', 'hybrid'),
        ('master', 'Project coordinator', 'A completed Master degree is required.', 'hybrid'),
        ('onsite', 'Project coordinator', '', 'onsite'),
        ('remote', 'Project coordinator', '', 'remote'),
        ('sparse', 'Project coordinator', '', None),
    ]
    jobs = [dict(id=key, title=title, description=None if key == 'sparse' else
                 duties + '<h3>Requirements</h3><ul><li>' + requirement + '</li></ul>',
                 work_arrangement=style, url=f'https://example.invalid/jobs/{key}')
            for key, title, requirement, style in cases]
    # Counterfactual only: these are NOT new facts about the fixture's applicant.
    confirmed = deepcopy(profile)
    confirmed.update(experience_years=2, work_styles=['remote', 'hybrid'])
    variants = {'saved_fixture': profile, 'hypothetical_two_years_remote_or_hybrid': confirmed}
    results = {}
    for variant, candidate in variants.items():
        results[variant] = {job['id']: matching.match_job(job, candidate) for job in jobs}
    assert results['saved_fixture']['tenure_5']['eligible']
    assert 'Profile experience not provided' in results['saved_fixture']['tenure_5']['unknowns']
    assert not results['hypothetical_two_years_remote_or_hybrid']['tenure_5']['eligible']
    assert not results['hypothetical_two_years_remote_or_hybrid']['onsite']['eligible']
    assert not results['saved_fixture']['language_c1']['eligible']
    assert results['saved_fixture']['language_optional']['eligible']
    assert any('SolidWorks' in gap for gap in results['saved_fixture']['academic_cad']['requirement_gaps'])
    assert any('Master' in gap for gap in results['saved_fixture']['master']['requirement_gaps'])
    assert results['saved_fixture']['sparse']['fit_tier'] != 'recommended'
    # This is a retrieval packet, NOT an AI-tailored or rendered CV.
    match = results['saved_fixture']['base']
    evidence = {skill: row for skill, row in profile['skill_evidence'].items()
                if matching.normalize_skills([skill])[0] in match['matched_skills']}
    packet = {
        'kind': 'evidence_selection_only',
        'job_id': 'base', 'job_description': jobs[0]['description'],
        'approved_profile_excerpt': {'summary': profile['summary'], 'skill_evidence': evidence,
                                    'language_levels': profile['language_levels']},
        'missing_for_complete_cv': ['verified employment timeline', 'identity/contact details',
                                    'dated achievement bullets', 'original CV layout'],
        'unknown_answers': ['work authorization', 'notice period', 'salary expectations'],
    }
    return {
        'rule_version': matching.VERSION,
        'matcher_sha256': hashlib.sha256(Path(matching.__file__).read_bytes() +
                                         (ROOT / 'hiring_scraper/requirements.py').read_bytes()).hexdigest(),
        'profile_sha256': hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        'jobs_sha256': hashlib.sha256(json.dumps(jobs, sort_keys=True).encode()).hexdigest(),
        'synthetic_job_count': len(jobs), 'variants': results, 'example_evidence_packet': packet,
    }


FILL = r"""(candidate) => {
  const aliases = {
    first_name: ['first name', 'vorname'], last_name: ['last name', 'nachname'],
    email: ['email', 'e-mail', 'e-mail-adresse'], phone: ['phone', 'telefon'],
    city: ['city', 'wohnort']
  };
  const filled = [], preserved = [], manual = [];
  for (const field of document.querySelectorAll('input, textarea, select')) {
    const label = (field.labels?.[0]?.textContent || field.getAttribute('aria-label') || '').trim().toLowerCase();
    const key = Object.keys(aliases).find(key => aliases[key].includes(label));
    if (!field.getClientRects().length || field.disabled || field.readOnly ||
        !['text', 'email', 'tel'].includes(field.type)) { manual.push(field.id); continue; }
    if (field.value) { preserved.push(field.id); continue; }
    if (!key || !candidate[key]) { manual.push(field.id); continue; }
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(field, candidate[key]);
    field.dispatchEvent(new Event('input', {bubbles: true}));
    field.dispatchEvent(new Event('change', {bubbles: true}));
    filled.push(field.id);
  }
  return {filled, preserved, manual, values: Object.fromEntries(
    [...document.querySelectorAll('input, textarea, select')].map(field => [field.id,
      field.type === 'checkbox' ? field.checked : field.value])), submissions: window.submissions,
      inputEvents: window.inputEvents};
}"""


def form(body):
    return ('<!doctype html><html><body><form onsubmit="window.submissions++; return false">' + body +
            '<button type="submit">Submit</button></form><script>window.submissions=0;'
            'window.inputEvents=0;document.addEventListener("input",()=>window.inputEvents++);'
            '</script></body></html>')


def browser_trial(executable_path=None):
    from playwright.sync_api import sync_playwright
    candidate = dict(first_name='Alex', last_name='Example', email='alex@example.invalid',
                     phone='+49 000 000000', city='Heidelberg')
    fixtures = {
        'english': form('<label>First name<input id="first"></label>'
                        '<label>Last name<input id="last"></label>'
                        '<label>Email<input type="email" id="email"></label>'
                        '<label>Phone<input type="tel" id="phone"></label>'),
        'german': form('<label>Vorname<input id="first"></label>'
                       '<label>Nachname<input id="last"></label>'
                       '<input aria-label="E-Mail-Adresse" type="email" id="email">'
                       '<label>Telefon<input id="phone"></label><label>Wohnort<input id="city"></label>'),
        'review_required': form('<label>First name<input id="first" value="Edited by applicant"></label>'
                               '<label>Email<input id="email" type="email"></label>'
                               '<label>Contact person<input id="ambiguous"></label>'
                               '<label>Salary expectations<input id="salary"></label>'
                               '<label>Work authorization<select id="authorization"><option value="">Choose</option>'
                               '<option>Yes</option></select></label>'
                               '<label>Accept data processing<input id="consent" type="checkbox"></label>'
                               '<label>CV<input id="cv" type="file"></label>'
                               '<label>Why this company?<textarea id="motivation"></textarea></label>'
                               '<label style="display:none">Email<input id="honeypot"></label>'
                               '<label>Password<input id="password" type="password"></label>'),
    }
    with sync_playwright() as pw:
        options = {'headless': True, 'args': ['--no-sandbox']}
        if executable_path:
            options['executable_path'] = executable_path
        browser = pw.chromium.launch(**options)
        try:
            page = browser.new_page()
            # Every request is intercepted locally. No employer receives any data.
            def respond(route):
                url = route.request.url
                if 'workspace.example.invalid' in url:
                    body = '<iframe src="https://employer.example.invalid/english"></iframe>'
                else:
                    body = fixtures[url.rsplit('/', 1)[-1]]
                route.fulfill(body=body, content_type='text/html')
            page.route('**/*', respond)
            page.goto('https://workspace.example.invalid/')
            page.frame_locator('iframe').locator('#first').wait_for()
            boundary = page.evaluate('''() => {try {
                document.querySelector('iframe').contentWindow.document.body;
                return 'unexpected_access';
            } catch (error) {return error.name;}}''')
            assert boundary == 'SecurityError'
            outcomes = {}
            for name in fixtures:
                page.goto(f'https://employer.example.invalid/{name}')
                outcomes[name] = page.evaluate(FILL, candidate)
                assert outcomes[name]['submissions'] == 0
                assert outcomes[name]['inputEvents'] == len(outcomes[name]['filled'])
            assert len(outcomes['english']['filled']) == 4
            assert len(outcomes['german']['filled']) == 5
            reviewed = outcomes['review_required']
            assert reviewed['filled'] == ['email']
            assert reviewed['preserved'] == ['first']
            assert reviewed['values']['consent'] is False
            assert all(reviewed['values'][key] == '' for key in
                       ['salary', 'authorization', 'ambiguous', 'cv', 'motivation', 'honeypot', 'password'])
            return {'scope': 'synthetic DOM injection, not a packaged extension or live ATS test',
                    'browser_version': browser.version, 'cross_origin_parent_access': boundary,
                    'forms': outcomes, 'external_requests': 0, 'submissions': 0}
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--chromium', help='Optional existing Chromium executable')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'reports/application-streamlining-trial.json')
    args = parser.parse_args()
    result = {'run_at': datetime.now(timezone.utc).isoformat(),
              'limitations': ['Synthetic diagnostics, not recommendation precision or hiring outcomes.',
                              'No OAuth login, model inference, CV generation or real ATS submission tested.'],
              'matching': matching_trial()}
    if args.browser:
        result['browser'] = browser_trial(args.chromium)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'output': str(args.output), 'synthetic_jobs': result['matching']['synthetic_job_count'],
                      'browser_forms': len(result.get('browser', {}).get('forms', {})),
                      'status': 'assertions passed'}))


if __name__ == '__main__':
    main()
