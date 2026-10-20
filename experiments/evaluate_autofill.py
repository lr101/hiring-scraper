"""Run the frozen expanded corpus against actual browser-injected runtime.

Example:
  .venv/bin/python experiments/evaluate_autofill.py --output /tmp/before.json
  .venv/bin/python experiments/evaluate_autofill.py --runtime-dir /tmp/baseline --output /tmp/baseline.json
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from experiments.autofill_corpus import manifest
from playwright.sync_api import sync_playwright


def evaluate(runtime):
    corpus=manifest()
    signature=hashlib.sha256(json.dumps(corpus,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium',headless=True)
        try:
            for case in corpus:
                page=browser.new_page();errors=[];requests=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/*',lambda route:(requests.append(route.request.url),route.abort()))
                try:
                    page.set_content(case['html'])
                    for name in ['history.js','best-effort.js']:page.add_script_tag(path=str(runtime/name))
                    started=time.perf_counter()
                    run=page.evaluate('''async ({profile,ids,repeat})=>{
                      const read=()=>Object.fromEntries(ids.map(id=>{let el=document.getElementById(id);return [id,['checkbox','radio'].includes(el.type)?el.checked:el.value]}));
                      await applicationHistory.prepare(profile);
                      const report=await applicationFields.run(profile);
                      const first=read();let second=null;
                      if(repeat){await applicationHistory.prepare(profile);await applicationFields.run(profile);second=read()}
                      return {first,second,submits:window.submits,fields:report.fields};
                    }''',{'profile':case['profile'],'ids':list(case['expected']),'repeat':case['repeat']})
                    records=[]
                    for identifier,expected in case['expected'].items():
                        initial=case['initial'].get(identifier,False if isinstance(expected,bool) else '')
                        actual=run['first'][identifier]
                        outcome=('preserved' if expected==initial else 'filled') if actual==expected else 'missed' if actual==initial else 'wrong'
                        records.append({'id':identifier,'initial':initial,'expected':expected,'actual':actual,'outcome':outcome})
                    results.append({'name':case['name'],'family':case['family'],'semantic_example':case['semantic_example'],
                                    'fields':records,'repeat_stable':run['second'] is None or run['first']==run['second'],
                                    'submits':run['submits'],'page_errors':errors,'network_requests':requests,
                                    'milliseconds':round((time.perf_counter()-started)*1000,2)})
                finally:page.close()
        finally:browser.close()
    total=Counter(field['outcome'] for case in results for field in case['fields'])
    families={family:dict(Counter(field['outcome'] for case in results if case['family']==family for field in case['fields'])) for family in sorted({case['family'] for case in results})}
    return {'scope':'Synthetic regression corpus, not a representative ATS accuracy estimate. Unscored support anchors excluded.',
            'manifest_sha256':signature,'runtime_sha256':{name:hashlib.sha256((runtime/name).read_bytes()).hexdigest() for name in ['history.js','best-effort.js']},
            'forms':len(corpus),'scored_fields':sum(len(case['fields']) for case in results),
            'semantic_examples':len({(case['family'],case['semantic_example']) for case in results}),
            'outcomes':dict(total),'families':families,
            'unstable_repeats':sum(not case['repeat_stable'] for case in results),
            'unexpected_submits':sum(case['submits'] for case in results),'page_errors':sum(len(case['page_errors']) for case in results),
            'network_requests':sum(len(case['network_requests']) for case in results),'cases':results}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir',type=Path,default=ROOT/'extensions/application-companion')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=evaluate(args.runtime_dir)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:value for key,value in result.items() if key!='cases'},indent=2))
