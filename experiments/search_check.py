"""Validate six manually curated search hits; does not automate a search engine."""
import json
import time
from pathlib import Path
from probe import probe

base=Path('data/karlsruhe')
results=[]
for i,seed in enumerate(json.loads((base/'search_seeds.json').read_text())):
    url=seed['url']
    row=dict(seed)
    time.sleep(1)
    meta,body=probe(url,base/'search',f'{i:02d}_career'); row['status']=meta.get('status'); row['final_url']=meta.get('final_url')
    results.append(row)
    (base/'search_results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n')
    time.sleep(1)
