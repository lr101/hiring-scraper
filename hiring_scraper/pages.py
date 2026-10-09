"""Static HTML discovery: links, embeds and structured data; no JS execution."""
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from .ats import identify

CAREER = re.compile(r'karriere|careers?|\bjobs?\b|jobangebote?|stellenangebote?|stellenmarkt|arbeitgeber|employer|vacanc|open.positions|offene.stellen|arbeiten.bei|join.us|jobboerse|traumjobs?|werde.teil|we.re.hiring|join.our.team|work\s+at|apply.now|open.roles', re.I)
ABOUT = re.compile(r'unternehmen|über.uns|ueber.uns|about|company|\bteam\b', re.I)
NON_CAREER_PATH = re.compile(r'(?:^|/)(?:privacy(?:-policy)?|datenschutz|legal|terms?|agb|impressum|imprint|cookies?|life-at(?:-[^/]*)?|values?(?:-[^/]*)?|culture|news|blog|aktuelles(?:-[^/]*)?|publikationen|presse)(?:/|$)', re.I)
SOCIAL = ('linkedin.com','xing.com','instagram.com','facebook.com','youtube.com','twitter.com','indeed.com','stepstone.de','kununu.com')
JOB_DETAIL_PATH = re.compile(r'(?:^|/)(?:job|jobangebote?|stellenangebot|stellenanzeige|vacanc(?:y|ies)|position)/[^/]+', re.I)
APPLICATION_PATH = re.compile(r'(?:^|/)(?:apply|application|bewerbung)(?:/|$)', re.I)
STRONG_JOB_LINK = re.compile(
    r'jobsuche|job[-\s]?search|job\s+listings?|vacanc(?:y|ies)|stellen(?:aus)?schreib|'
    r'stellenangebote?|offene\s+stellen|current\s+(?:jobs?|vacanc)|open\s+positions|'
    r'find\s+(?:your\s+)?(?:next\s+)?jobs?|jetzt\s+bewerben|apply\s+now', re.I)


def clean_url(base, href):
    if not isinstance(href, str):
        return None
    href = html.unescape(href).strip()
    if not href or href.startswith('#'):
        return None
    try:
        p = urlsplit(urljoin(base, href))
    except ValueError:
        return None
    if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password:
        return None
    if '{{' in unquote(p.path) or '}}' in unquote(p.path):
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
        target_path = urlsplit(target).path
        path_label = target_path+' '+label
        job_detail = bool(JOB_DETAIL_PATH.search(target_path))
        application_route = bool(APPLICATION_PATH.search(target_path)) or bool(
            re.search(r'(?:^|[?&])gh_jid=\d+(?:&|$)', urlsplit(target).query, re.I))
        strong_job_link = bool(STRONG_JOB_LINK.search(path_label))
        score = (100 if provider else 92 if job_detail or application_route else
                 86 if strong_job_link else
                 70 if CAREER.search(path_label) else 20 if ABOUT.search(path_label) else 0)
        if score:
            candidates.setdefault(target, {'url':target,'label':' '.join(label.split())[:140],
                                           'score':score,'evidence_kind':'link'})
    for raw,kind in doc.embeds:
        target = clean_url(url,raw)
        if target: remember(target,kind)
    postings = 0
    for kind, script in doc.scripts:
        if kind == 'application/ld+json':
            try:
                structured = json.loads(script)
                postings += count_postings(structured)
                # A publisher's explicit search target can be absent from visible
                # navigation (Phenom uses a hidden anchor and a JS search form).
                values = structured if isinstance(structured, list) else [structured]
                for item in values:
                    if not isinstance(item, dict) or item.get('@type') != 'WebSite':
                        continue
                    actions = item.get('potentialAction', [])
                    for action in actions if isinstance(actions, list) else [actions]:
                        if not isinstance(action, dict) or action.get('@type') != 'SearchAction':
                            continue
                        raw = action.get('target')
                        if isinstance(raw, dict):
                            raw = raw.get('urlTemplate')
                        if not isinstance(raw, str):
                            continue
                        target = clean_url(url, raw.replace('{search_term_string}', ''))
                        if not target or re.search(r'[{}]', target):
                            continue
                        parts, origin = urlsplit(target), urlsplit(url)
                        if ((parts.scheme, parts.netloc) != (origin.scheme, origin.netloc) or
                                not re.search(r'/(?:search-results|jobs|jobsuche|job-search)/?$', parts.path, re.I)):
                            continue
                        candidates.setdefault(target, {'url':target, 'label':'Job search', 'score':96,
                                                       'evidence_kind':'schema_org_searchaction'})
            except (ValueError, TypeError): pass
        unescaped = html.unescape(script.replace('\\/', '/').replace('\\u0026','&'))
        for target in re.findall(r'https?://[^\s"\'<>\\]+', unescaped):
            target = clean_url(url,target)
            if target: remember(target,'script_reference')
    path = urlsplit(url).path
    noise_path = bool(NON_CAREER_PATH.search(path))
    is_career_heading = bool(CAREER.search(' '.join(doc.h1))) or (not doc.h1 and bool(CAREER.search(title)))
    is_career_route = not noise_path and bool(CAREER.search(path))
    career_context = not noise_path and (is_career_heading or is_career_route)
    recruitment = bool(re.search(
        r'bewerb|apply|hiring|jobangebote?|stellenangebote?|offene.stellen|open.positions|vacanc|'
        r'benefits|mitarbeiter|your.team|looks?\s+for\s+(?:talents?|employees|people|you)|'
        r'working\s+students?|master\s+theses?', text, re.I))
    if re.search(r'your content goes here|edit or remove this text|lorem ipsum',text,re.I) and career_context:
        classification = 'placeholder'
    elif postings:
        classification = 'jobposting'
    elif career_context and (recruitment or ats):
        classification = 'career_content'
    else:
        classification = 'ordinary_page'
    return {'title':title[:200], 'headings':headings[:400], 'classification':classification,
            'jobposting_count':postings, 'ats':list(ats.values()),
            'candidates':sorted(candidates.values(), key=lambda x:(-x['score'], len(x['url'])))[:60]}
