"""Read-only public-form reconnaissance. No typing, upload, consent or navigation clicks.

Only GET/HEAD requests are allowed. Reproduce with the project's Playwright environment:
  python -m experiments.application_companion_dry_run --output /tmp/application-form-trials.json
Use intercepted synthetic forms for mutation/fill/attachment trials (test_form_preparation).
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('Greenhouse wrapper', 'https://www.mail-and-media.com/jobs/bewerbung?gh_jid=4399833101'),
    ('Greenhouse direct posting', 'https://job-boards.eu.greenhouse.io/mailmediaportal/jobs/4399833101'),
    ('Lever', 'https://jobs.eu.lever.co/engelvoelkers/e8c91d6d-2775-4fb5-b58e-1b810d5ebc30/apply'),
    ('Personio', 'https://amiconsult.jobs.personio.de/job/1851542?display=en#apply'),
]

def sanitized(value):
    if isinstance(value, dict): return {key:sanitized(item) for key,item in value.items()}
    if isinstance(value, list): return [sanitized(item) for item in value]
    if isinstance(value, str) and value.startswith(('https://','http://')):
        parts=urlsplit(value)
        query=urlencode([(key,item) for key,item in parse_qsl(parts.query) if key.lower() in ('gh_jid','for','display','jobid','job_id')])
        return urlunsplit((parts.scheme,parts.netloc,parts.path,query,''))
    return value

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', default='/tmp/application-form-trials.json')
    args = parser.parse_args()
    result = {'at': datetime.now(timezone.utc).isoformat(), 'mode': 'read-only GET/HEAD; no applicant values', 'cases': []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chromium', headless=True)
        context = browser.new_context(service_workers="block")
        for provider, url in CASES:
            blocked = []
            def route(request_route):
                if request_route.request.method not in ('GET', 'HEAD'):
                    blocked.append({'method':request_route.request.method, 'origin':request_route.request.url.split('?')[0]})
                    request_route.abort()
                else: request_route.continue_()
            context.route('**/*', route)
            page = context.new_page(); item = {'provider':provider, 'source_url':url, 'filled':0, 'files_selected':0, 'submissions':0}
            try:
                response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(3000)
                item.update(status=response.status if response else None, final_url=page.url, title=page.title())
                for name in ('page-context.js', 'history.js', 'best-effort.js'):
                    page.evaluate((ROOT/'extensions/application-companion'/name).read_text())
                item['top_document'] = page.evaluate('''async()=>{
                    try { const form=await applicationFields.documentTargets(); return {form_id:form.form_id,fields:form.fields,hidden_uploads:form.hidden_uploads}; }
                    catch(error) {return {reason:error.message,forms:document.forms.length,native_controls:document.querySelectorAll('input,textarea,select').length,iframes:[...document.querySelectorAll('iframe')].map(frame=>({title:frame.title,src:frame.src.split('?')[0]}))};}
                }''')
                item['frames'] = [{'url':frame.url.split('?')[0].split('#')[0], 'native_controls':frame.locator('input,textarea,select').count()} for frame in page.frames[1:] if frame.url.startswith('https://')]
                embed=page.locator('iframe[title="Greenhouse Job Board"]')
                if embed.count()==1:
                    direct=context.new_page()
                    try:
                        direct_url=embed.get_attribute('src')
                        if direct_url and direct_url.startswith('https://job-boards.eu.greenhouse.io/'):
                            direct_response=direct.goto(direct_url,wait_until='domcontentloaded',timeout=30000)
                            direct.wait_for_timeout(2000)
                            for name in ('page-context.js','history.js','best-effort.js'):
                                direct.evaluate((ROOT/'extensions/application-companion'/name).read_text())
                            item['embedded_direct']={'status':direct_response.status,'url':direct_url,'form':direct.evaluate('async()=>{try{return await applicationFields.documentTargets()}catch(error){return {reason:error.message}}}')}
                    finally:direct.close()

            except Exception as error:
                item['error'] = str(error).splitlines()[0][:500]
            finally:
                item['blocked_non_read_requests'] = blocked
                result['cases'].append(item)
                Path(args.output).write_text(json.dumps(sanitized(result),indent=2)+'\n')
                print(json.dumps({'provider':provider,'status':item.get('status'),'fields':len(item.get('top_document',{}).get('fields',[])),'error':item.get('error')}),flush=True)
                page.close(); context.unroute('**/*',route)
        context.close(); browser.close()

if __name__ == '__main__': main()
