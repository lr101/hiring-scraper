"""A process-level Codex fixture: no OpenAI requests or real credentials."""
from pathlib import Path


def write_codex_fixture(folder):
    executable = Path(folder) / 'fake-codex'
    executable.write_text('''#!/usr/bin/env python3
import json, os, sys, time
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
if 'exec' in args:
    text=sys.stdin.read()
    (root/'seen.json').write_text(json.dumps({'args':args,'env':dict(os.environ),'prompt':text,
        'schema':json.loads(Path(args[args.index('--output-schema')+1]).read_text()),'cwd':os.getcwd()}))
    if settings.get('delay'):time.sleep(settings['delay'])
    if settings.get('fail'): print('private-token-do-not-expose',file=sys.stderr);sys.exit(1)
    Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(settings.get('result',{'answer':'prepared'})))
    sys.exit(0)
sys.exit(2)
''')
    executable.chmod(0o700)
    return executable
