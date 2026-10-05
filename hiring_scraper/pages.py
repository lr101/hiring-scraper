"""Static HTML discovery: links, embeds and structured data; no JS execution."""
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from .ats import identify

CAREER = re.compile(r'karriere|careers?|\bjobs?\b|stellenangebote?|stellenmarkt|vacanc|open.positions|offene.stellen|arbeiten.bei|join.us|jobboerse|traumjobs?|werde.teil|we.re.hiring|join.our.team|apply.now|open.roles', re.I)
ABOUT = re.compile(r'unternehmen|über.uns|ueber.uns|about|company|\bteam\b', re.I)
SOCIAL = ('linkedin.com','xing.com','instagram.com','facebook.com','youtube.com','twitter.com','indeed.com','stepstone.de','kununu.com')


def clean_url(base, href):
    href = html.unescape(href).strip()
    if not href or href.startswith('#'):
        return None
    p = urlsplit(urljoin(base, href))
    if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password:
        return None
    query = urlencode([(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
                       if not k.startswith('utm_') and k not in {'gh_src','source'}])
    return urlunsplit((p.scheme, p.netloc.lower(), p.path or '/', query, ''))


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.embeds = []
        self.scripts = []
        self.text = []
        self.headings = []
        self.title = []
        self.h1 = []
        self.in_h1 = False
        self.anchor = None
        self.script = None
        self.in_title = False
        self.in_heading = False
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'a':
            self.anchor = [a.get('href',''), a.get('aria-label','')]
        if tag in {'iframe','script'} and a.get('src'):
            self.embeds.append((a['src'], tag))
        for key, value in attrs:
            if key.startswith('data-') and value and ('http' in value):
                self.embeds.append((value, 'data_attribute'))
        if tag == 'meta' and a.get('http-equiv','').lower() == 'content-security-policy':
            for tenant_host in re.findall(r'[A-Za-z0-9_-]+\.jobs\.personio\.(?:de|com)', a.get('content','')):
                self.embeds.append(('https://'+tenant_host, 'csp_hint'))
        if tag == 'script':
            self.script = [a.get('type',''), '']
        if tag in {'script','style','svg'}:
            self.hidden += 1
        if tag == 'title': self.in_title = True
        if tag in {'h1','h2'}: self.in_heading = True
        if tag == 'h1': self.in_h1 = True

    def handle_data(self, data):
        if self.script is not None: self.script[1] += data
        if not self.hidden:
            self.text.append(data)
            if self.anchor is not None: self.anchor[1] += data
            if self.in_title: self.title.append(data)
            if self.in_heading: self.headings.append(data)
            if self.in_h1: self.h1.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor is not None:
            self.links.append(self.anchor)
            self.anchor = None
        if tag == 'script' and self.script is not None:
            self.scripts.append(self.script)
            self.script = None
        if tag in {'script','style','svg'}: self.hidden = max(0, self.hidden-1)
        if tag == 'title': self.in_title = False
        if tag in {'h1','h2'}: self.in_heading = False
        if tag == 'h1': self.in_h1 = False


def count_postings(value):
    if isinstance(value, dict):
        types = value.get('@type', [])
        match = types == 'JobPosting' or (isinstance(types, list) and 'JobPosting' in types)
        return int(match) + sum(count_postings(v) for v in value.values() if isinstance(v, (dict,list)))
    if isinstance(value, list): return sum(count_postings(v) for v in value)
    return 0


def inspect_page(url, source):
    doc = Document()
    doc.feed(source)
    ats = {}
    candidates = {}
    title = ' '.join(' '.join(doc.title).split())
    headings = ' '.join(' '.join(doc.headings).split())
    text = ' '.join(' '.join(doc.text).split())

    def remember(target, kind):
        provider = identify(target)
        if provider and provider['feed_url'] is None and kind not in {'link','iframe'}:
            return None
        if provider:
            key = (provider['provider'], provider['board_url'])
            ats.setdefault(key, {**provider, 'evidence_url':target, 'evidence_kind':kind})
        return provider

    for href, label in doc.links:
        target = clean_url(url, href)
        if not target or target == clean_url(url, url): continue
        host = urlsplit(target).hostname
        if any(host == d or host.endswith('.'+d) for d in SOCIAL): continue
        if re.search(r'\.(pdf|jpg|png|zip|svg)(?:\?|$)',target,re.I): continue
        provider = remember(target, 'link')
        score = 100 if provider else 70 if CAREER.search(href+' '+label) else 20 if ABOUT.search(href+' '+label) else 0
        if score:
            candidates.setdefault(target, {'url':target,'label':' '.join(label.split())[:140],
                                           'score':score,'evidence_kind':'link'})
    for raw,kind in doc.embeds:
        target = clean_url(url,raw)
        if target: remember(target,kind)
    postings = 0
    for kind, script in doc.scripts:
        if kind == 'application/ld+json':
            try: postings += count_postings(json.loads(script))
            except (ValueError, TypeError): pass
        unescaped = html.unescape(script.replace('\\/', '/').replace('\\u0026','&'))
        for target in re.findall(r'https?://[^\s"\'<>\\]+', unescaped):
            target = clean_url(url,target)
            if target: remember(target,'script_reference')
    is_career_heading = bool(CAREER.search(title+' '+' '.join(doc.h1)))
    recruitment = bool(re.search(r'bewerb|apply|hiring|offene.stellen|open.positions|vacanc|benefits|mitarbeiter|your.team',text,re.I))
    if re.search(r'your content goes here|edit or remove this text|lorem ipsum',text,re.I) and is_career_heading:
        classification = 'placeholder'
    elif postings:
        classification = 'jobposting'
    elif is_career_heading and (recruitment or ats):
        classification = 'career_content'
    else:
        classification = 'ordinary_page'
    return {'title':title[:200], 'headings':headings[:400], 'classification':classification,
            'jobposting_count':postings, 'ats':list(ats.values()),
            'candidates':sorted(candidates.values(), key=lambda x:(-x['score'], len(x['url'])))[:60]}
