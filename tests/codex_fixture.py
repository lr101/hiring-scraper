"""A process-level Codex fixture: no OpenAI requests or real credentials."""
from pathlib import Path


def write_codex_fixture(folder):
    executable = Path(folder) / 'fake-codex'
    executable.write_text('''#!/usr/bin/env python3
import json, os, sys, time, subprocess
from pathlib import Path
home=Path(os.environ['CODEX_HOME']); home.mkdir(parents=True,exist_ok=True)
root=home.parent
args=sys.argv[1:]
settings=json.loads((root/'fixture.json').read_text()) if (root/'fixture.json').exists() else {}
if 'status' in args:
    auth=json.loads((home/'auth.json').read_text()) if (home/'auth.json').exists() else {}
    if auth.get('auth_mode')=='chatgpt': print('Logged in using ChatGPT'); sys.exit(0)
    if auth.get('auth_mode')=='apikey': print('Logged in using an API key'); sys.exit(0)
    print('Not logged in'); sys.exit(1)
if '--device-auth' in args:
    print('Open https://auth.openai.com/codex/device',flush=True)
    print('Enter this one-time code: ABCD-EFGH',flush=True)
    while not (root/'allow-login').exists(): time.sleep(.02)
    (home/'auth.json').write_text(json.dumps({'auth_mode':'chatgpt'}))
    print('Successfully logged in',flush=True); sys.exit(0)
if 'logout' in args:
    (home/'auth.json').unlink(missing_ok=True); sys.exit(0)
if 'app-server' in args:
    (root/'app-server.pid').write_text(str(os.getpid()))
    if settings.get('usage_exit_with_child'):
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
        (root/'usage-child.pid').write_text(str(child.pid))
        sys.exit(1)
    seen={'args':args,'env':dict(os.environ),'messages':[]}
    for line in sys.stdin:
        request=json.loads(line)
        seen['messages'].append(request)
        (root/'usage-seen.json').write_text(json.dumps(seen))
        method=request['method']
        if method=='initialized': continue
        if method=='initialize': result={'userAgent':'fixture','platformFamily':'unix','platformOs':'linux'}
        elif method=='account/read':
            auth=json.loads((home/'auth.json').read_text()) if (home/'auth.json').exists() else {}
            account={'type':'chatgpt','email':'private@example.invalid','planType':'plus'} if auth.get('auth_mode')=='chatgpt' else {'type':'apiKey'}
            result={'account':settings.get('account',account),'requiresOpenaiAuth':True}
        elif method=='account/rateLimits/read':
            if settings.get('usage_delay'): time.sleep(settings['usage_delay'])
            if settings.get('usage_raw'):
                print(settings['usage_raw'],flush=True); continue
            if settings.get('usage_bytes'):
                print('x'*settings['usage_bytes'],flush=True); continue
            if settings.get('usage_error'):
                print(json.dumps({'id':request['id'],'error':{'code':-32000,'message':'private-token-do-not-expose'}}),flush=True); continue
            result=settings.get('quota_payload',{'rateLimits':{'limitId':'codex','limitName':None,'primary':{'usedPercent':49,'windowDurationMins':300,'resetsAt':int(time.time())+3600},'secondary':{'usedPercent':12,'windowDurationMins':10080,'resetsAt':int(time.time())+86400},'credits':None,'planType':'plus'},'rateLimitsByLimitId':None})
        else: sys.exit(3)
        print(json.dumps({'id':request['id'],'result':result}),flush=True)
    sys.exit(0)
if 'exec' in args:
    text=sys.stdin.read()
    (root/'seen.json').write_text(json.dumps({'args':args,'env':dict(os.environ),'prompt':text,
        'schema':json.loads(Path(args[args.index('--output-schema')+1]).read_text()),'cwd':os.getcwd()}))
    if settings.get('delay'):time.sleep(settings['delay'])
    if settings.get('fail'):
        print(settings.get('stdout', ''),file=sys.stdout)
        print(settings.get('stderr', 'private-token-do-not-expose'),file=sys.stderr);sys.exit(settings.get('exit_code', 1))
    if settings.get('missing_output'): sys.exit(0)
    Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(settings.get('result',{'answer':'prepared'})))
    sys.exit(0)
sys.exit(2)
''')
    executable.chmod(0o700)
    return executable
