"""Small, sequential HTTP probe; saves evidence, never retries challenges."""
import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = 'HiringDiscoveryResearch/0.1 (local feasibility experiment)'

def probe(url, out, label, headers=None):
    start = time.monotonic()
    record = {'url': url, 'checked_at': datetime.now(timezone.utc).isoformat(), 'user_agent': UA}
    request = urllib.request.Request(url, headers={'User-Agent': UA, **(headers or {})})
    try:
        response = urllib.request.urlopen(request, timeout=45)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, ValueError) as error:
        record.update(error=str(error), elapsed_seconds=round(time.monotonic()-start, 2))
        response = None
    body = b''
    if response is not None:
        with response:
            body = response.read(5_000_001)
            record.update(status=response.code, final_url=response.url,
                          content_type=response.headers.get('Content-Type'),
                          bytes=len(body), truncated=len(body)>5_000_000,
                          elapsed_seconds=round(time.monotonic()-start, 2),
                          sha256=hashlib.sha256(body).hexdigest())
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / (label+'.body')).write_bytes(body)
    (out / (label+'.json')).write_text(json.dumps(record, indent=2)+'\n')
    print(label, json.dumps(record), flush=True)
    return record, body

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--out', default='data/probes')
    parser.add_argument('--label', default='probe')
    args = parser.parse_args()
    probe(args.url, args.out, args.label)
