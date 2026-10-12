"""Probe a fixed 12-site OSM IT sample for homepage career links."""
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from probe import probe

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.current=None
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            self.current=[dict(attrs).get('href',''),'']
    def handle_data(self,data):
        if self.current is not None: self.current[1]+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.current is not None:
            self.links.append(self.current); self.current=None

def career_links(parser, base_url):
    matches=[]
    for href,title in parser.links:
        if not href.strip(): continue
        link=urljoin(base_url,href)
        if urlsplit(link).scheme not in ('https','http'): continue
        if re.search(r'karriere|career|\bjobs\b|stellenangebote|stellenangebot|join.us',href+' '+title,re.I) and link not in matches:
            matches.append(link)
    return matches

def main():
    base=Path('data/karlsruhe')
    sample=json.loads((base/'osm_sample.json').read_text())
    out=base/'careers'; results=[]
    for i,e in enumerate(sample):
        tags=e['tags']; original=tags.get('website',tags.get('contact:website'))
        # Test HTTPS explicitly for historic http OSM tags; preserve original as evidence.
        url=re.sub(r'^http:', 'https:', original)
        row={'name':tags['name'],'osm_url':f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
             'tagged_website':original,'tested_website':url,'career_links':[]}
        label=f'{i:02d}'
        meta,body=probe(url,out,label+'_home'); row['homepage_status']=meta.get('status')
        if meta.get('status')==200:
            parser=Links(); parser.feed(body.decode('utf-8',errors='replace'))
            matches=career_links(parser, meta['final_url'])
            row['career_links']=matches
            row['result']='career_link_found' if matches else 'no_homepage_career_link'
            if matches:
                career=matches[0]
                time.sleep(1)
                cm,cb=probe(career,out,label+'_career'); row['career_status']=cm.get('status');row['career_final_url']=cm.get('final_url')
        else: row['result']='homepage_not_accessible'
        results.append(row)
        (base/'career_sample_results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n')
        time.sleep(1)
    (base/'career_sample_results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n')

if __name__=='__main__':
    main()
