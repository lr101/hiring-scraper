"""Replay captured company forms offline. This command never downloads a page.

Initial HTML snapshots, provenance and independent expectations are stored in
reports/autofill-real. Site scripts/resources were removed during extraction;
this evaluates genuine form DOM, not the companies' live server workflows.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import gzip
import json
from pathlib import Path
import re

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / 'reports/autofill-real'
RUNTIME = ROOT / 'extensions/application-companion'

# These are deliberately installed before injecting the extension runtime.
GUARDS = """() => {
  window.audit = {submitAttempts: 0, transportAttempts: []};
  const deny = (kind, url) => {
    window.audit.transportAttempts.push({kind, url: String(url)});
    throw new Error('Offline evaluation: network transport denied');
  };
  window.fetch = (url) => {
    window.audit.transportAttempts.push({kind: 'fetch', url: String(url)});
    return Promise.reject(new Error('Offline evaluation: fetch denied'));
  };
  XMLHttpRequest.prototype.open = function(method, url) { deny('xhr', url); };
  navigator.sendBeacon = (url) => { deny('beacon', url); };
  window.WebSocket = class { constructor(url) { deny('websocket', url); } };
  window.EventSource = class { constructor(url) { deny('eventsource', url); } };
  const stop = function() { window.audit.submitAttempts++; };
  HTMLFormElement.prototype.submit = stop;
  HTMLFormElement.prototype.requestSubmit = stop;
  document.addEventListener('submit', event => {
    event.preventDefault(); event.stopImmediatePropagation(); stop();
  }, true);
}"""

RUN = """async profile => {
  const read = () => [...document.querySelectorAll('[data-real-field]')].map(el => ({
    index: Number(el.dataset.realField),
    value: ['checkbox','radio'].includes(el.type) ? el.checked : el.value,
    visible: !!el.getClientRects().length && !el.closest('[hidden],[aria-hidden=true]')
  }));
  const initial = read();
  const prepare = await applicationHistory.prepare(profile);
  const result = await applicationFields.run(profile);
  const first = read();
  await applicationHistory.prepare(profile);
  const repeated = await applicationFields.run(profile);
  return {initial, first, second: read(), prepare, report: result.fields,
          repeatedReport: repeated.fields, audit: window.audit};
}"""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches(actual, expectation):
    if expectation['rule'] == 'equals':
        return actual in expectation['values']
    normalize = lambda value: re.sub(r'\s+', ' ', str(value).casefold()).strip()
    return all(normalize(part) in normalize(actual) for part in expectation['parts'])


def verify_isolation(browser):
    """Exercise blocked transports and submission using only an offline page."""
    context = browser.new_context(offline=True, service_workers='block')
    blocked = []
    def deny(route):
        blocked.append(route.request.url)
        route.abort('blockedbyclient')
    context.route('**/*', deny)
    page = context.new_page()
    try:
        page.set_content('<form><button>Submit</button></form>')
        page.evaluate(GUARDS)
        audit = page.evaluate("""async () => {
          await fetch('https://offline-probe.invalid/fetch').catch(() => {});
          for (const action of [
            () => new XMLHttpRequest().open('POST', 'https://offline-probe.invalid/xhr'),
            () => navigator.sendBeacon('https://offline-probe.invalid/beacon', 'fictional'),
            () => new WebSocket('wss://offline-probe.invalid/socket'),
            () => new EventSource('https://offline-probe.invalid/events')
          ]) { try { action(); } catch {} }
          document.querySelector('form').submit();
          document.querySelector('form').requestSubmit();
          return window.audit;
        }""")
        assert len(audit['transportAttempts']) == 5, audit
        assert audit['submitAttempts'] == 2, audit
        assert not blocked, blocked
        # Independently verify browser interception, even if a JS guard is bypassed.
        from playwright.sync_api import Error
        try:
            page.goto('https://offline-probe.invalid/route-check', timeout=3000)
        except Error:
            pass
        assert blocked == ['https://offline-probe.invalid/route-check'], blocked
        return {'guarded_transports': 5, 'guarded_submissions': 2,
                'browser_route_abort_verified': True, 'allowed_requests': 0}
    finally:
        context.close()


def evaluate(data: Path):
    sources = json.loads((data / 'sources.json').read_text())
    expectations = json.loads((data / 'expectations.json').read_text())
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--disable-background-networking', '--disable-component-update',
                  '--disable-sync', '--no-first-run',
                  '--proxy-server=http://127.0.0.1:9'],
        )
        try:
            isolation = verify_isolation(browser)
            for source in sources:
                snapshot = data / source['snapshot']
                assert digest(snapshot) == source['snapshot_sha256'], snapshot
                context = browser.new_context(offline=True, service_workers='block')
                attempted = []
                def deny_route(route):
                    attempted.append({'method': route.request.method, 'url': route.request.url})
                    route.abort('blockedbyclient')
                context.route('**/*', deny_route)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                try:
                    markup = gzip.decompress(snapshot.read_bytes()).decode('utf-8') if snapshot.suffix == '.gz' else snapshot.read_text()
                    page.set_content(markup)
                    assert page.locator('script,iframe,object,embed').count() == 0
                    page.evaluate(GUARDS)
                    for name in ('history.js', 'best-effort.js'):
                        page.add_script_tag(path=str(RUNTIME / name))
                    run = page.evaluate(RUN, expectations['profile'])
                    initial = {x['index']: x for x in run['initial']}
                    actual = {x['index']: x for x in run['first']}
                    targets = expectations['forms'][source['company_id']]
                    fields = []
                    for field in source['controls']:
                        index = field['index']
                        # A select may carry an invalid type=submit attribute.
                        # It is still a real question, not a submit button.
                        if (field['tag'] == 'input' and
                                field['type'] in ('hidden', 'button', 'submit', 'reset')):
                            continue
                        expected = targets.get(str(index))
                        if index not in initial:
                            fields.append({**field, 'initial': None, 'actual': None,
                                           'visible': False,
                                           'expectation': expected or {'rule': 'preserve'},
                                           'outcome': 'unrendered'})
                            continue
                        before, after = initial[index]['value'], actual[index]['value']
                        if expected and not initial[index]['visible']:
                            outcome = 'not_visible' if after == before else 'wrong'
                        elif expected:
                            outcome = ('filled' if matches(after, expected) else
                                       'missed' if after == before else 'wrong')
                        else:
                            outcome = 'preserved' if after == before else 'wrong'
                        fields.append({**field, 'initial': before, 'actual': after,
                                       'visible': initial[index]['visible'],
                                       'expectation': expected or {'rule': 'preserve'},
                                       'outcome': outcome})
                    result = {
                        'company_id': source['company_id'], 'company': source['company'],
                        'url': source['url'], 'fields': fields,
                        'outcomes': dict(Counter(f['outcome'] for f in fields)),
                        'repeat_stable': run['first'] == run['second'],
                        'runtime_report': run['report'], 'prepare': run['prepare'],
                        'audit': {**run['audit'], 'blockedBrowserRequests': attempted,
                                  'allowedBrowserRequests': 0},
                        'page_errors': errors,
                    }
                    results.append(result)
                    print(source['company_id'], result['outcomes'], flush=True)
                finally:
                    context.close()
        finally:
            browser.close()
    fields = [field for result in results for field in result['fields']]
    return {
        'scope': 'Real initial-HTML DOM snapshots; original inline CSS retained; site scripts and external CSS absent. Not live ATS end-to-end testing.',
        'isolation_self_check': isolation,
        'forms': len(results), 'inspected_fields': len(fields),
        'scored_fields': sum(f['outcome'] not in ('unrendered', 'not_visible') for f in fields),
        'fillable_fields': sum(f['expectation']['rule'] != 'preserve' and
                              f['outcome'] not in ('unrendered', 'not_visible') for f in fields),
        'outcomes': dict(Counter(f['outcome'] for f in fields)),
        'by_category': {category: dict(Counter(f['outcome'] for f in fields
                         if f['expectation'].get('category') == category))
                        for category in ('contact', 'experience')},
        'unstable_repeats': sum(not r['repeat_stable'] for r in results),
        'submit_attempts': sum(r['audit']['submitAttempts'] for r in results),
        'transport_attempts': sum(len(r['audit']['transportAttempts']) for r in results),
        'blocked_browser_requests': sum(len(r['audit']['blockedBrowserRequests']) for r in results),
        'allowed_browser_requests': 0,
        'runtime_sha256': {name: digest(RUNTIME / name) for name in ('history.js', 'best-effort.js')},
        'expectations_sha256': digest(data / 'expectations.json'),
        'sources_sha256': digest(data / 'sources.json'),
        'results': results,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--output', type=Path, default=DEFAULT_DATA / 'results.json')
    args = parser.parse_args()
    report = evaluate(args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}, indent=2))
