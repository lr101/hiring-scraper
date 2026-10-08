"""Bounded homepage discovery, with identity evidence from the destination page."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from threading import Lock
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener

from hiring_scraper.http import NoRedirect
from hiring_scraper.osm_websites import is_public_hostname, resolve_osm_email_websites
from hiring_scraper.pages import Document, clean_url

LOG = logging.getLogger(__name__)
# Directories and job/social profiles identify a lead, not an employer-owned website.
THIRD_PARTY_HOSTS = {
    'linkedin.com', 'xing.com', 'facebook.com', 'instagram.com', 'youtube.com',
    'indeed.com', 'stepstone.de', 'kununu.com', 'companyhouse.de', 'implisense.com',
    'northdata.de', 'unternehmen24.info', 'firmenschau.com', 'dnb.com',
    'dasoertliche.de', 'dastelefonbuch.de', 'gelbeseiten.de', 'meinestadt.de',
    'stadtbranchenbuch.com', 'marktplatz-mittelstand.de', 'mapcarta.com',
    'openstreetmap.org', 'wikipedia.org', 'cyberforum.de', 'google.com',
}
LEGAL_NOISE = {'gmbh', 'ag', 'kg', 'co', 'mbh', 'ug', 'gbr', 'eg', 'ev',
               'haftungsbeschrankt', 'gesellschaft', 'limited', 'ltd', 'inc'}
GENERIC_NAME = {'software', 'media', 'security', 'transport', 'immobilien', 'management',
                'center', 'company', 'consulting', 'architekten', 'group', 'gruppe'}
PARKED = re.compile(r'domain (?:is )?for sale|buy this domain|domain kaufen|domain steht zum verkauf', re.I)
CONTACT = re.compile(r'impressum|imprint|kontakt|contact|about|uber.uns|ueber.uns', re.I)
_SEARCH_LOCK = Lock()
_LAST_SEARCH = 0.0


def phrase(value: str) -> str:
    normalized = unicodedata.normalize('NFKD', value.casefold().replace('ß', 'ss'))
    normalized = ''.join(c for c in normalized if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', normalized))


def tokens(value: str) -> set[str]:
    value = unicodedata.normalize('NFKD', value.casefold().replace('ß', 'ss'))
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return set(re.findall(r'[^\W_]+', value)) - LEGAL_NOISE


def public_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    url = clean_url(value, value)
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').casefold().rstrip('.')
        dns = host.encode('idna').decode('ascii')
        if (not is_public_hostname(host) or parsed.port not in (None, 80, 443) or
                len(dns) > 253 or '.' not in dns or
                any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                    for label in dns.split('.'))):
            return None
        if any(host == h or host.endswith('.' + h) for h in THIRD_PARTY_HOSTS):
            return None
        return url
    except (ValueError, UnicodeError):
        return None


def host(url: str) -> str:
    return (urlsplit(url).hostname or '').casefold().removeprefix('www.')


def root(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, '/', '', ''))


def _identity(candidate: dict, doc: Document, url: str, method: str, lead_url: str) -> bool:
    name = tokens(candidate['name'])
    distinctive = {t for t in name - GENERIC_NAME if len(t) >= 4 or (len(t) >= 3 and any(c.isdigit() for c in t))}
    if not distinctive:
        return False
    text = ' '.join(doc.text)
    visible = tokens(text)
    title = tokens(' '.join(doc.title + doc.h1))
    if PARKED.search(text) or not distinctive <= visible:
        return False
    domain_tokens = tokens(host(url).replace('.', ' '))
    brand = bool(_brand_tokens(candidate) & domain_tokens)
    # A renamed destination must identify the old company, not inherit email trust.
    email_same_host = method == 'email' and host(url) == host(lead_url)
    company_phrase = phrase(candidate['name'])
    legal_name = bool(set(company_phrase.split()) & {'gmbh', 'ag', 'kg', 'ug', 'gbr'})
    legal_match = not legal_name or (' ' + company_phrase + ' ') in (' ' + phrase(text) + ' ')
    if not legal_match:
        return False
    if email_same_host:
        # Map names may append an explanatory subtitle ("Schrifthof - Werkstatt ...").
        # The complete distinctive primary name must appear in the site's identity heading.
        primary_name = re.split(r'\s+[-–—]\s+', candidate['name'], maxsplit=1)[0]
        primary_tokens = tokens(primary_name) - GENERIC_NAME
        return bool(primary_tokens and primary_tokens <= title and
                    distinctive & tokens(host(lead_url)))
    # Search and cross-domain redirects require corroborating local identity.
    tags = candidate.get('tags') or {}
    city = tokens(str(tags.get('addr:city') or ''))
    postcode = str(tags.get('addr:postcode') or '')
    street = tokens(str(tags.get('addr:street') or ''))
    locality = bool(city and city <= visible)
    address = bool((postcode and postcode in visible) or (street and street <= visible))
    # One-word names are especially collision-prone: require a precise local clue.
    name_match = name <= visible
    # Shared brand tokens cannot establish a legal-entity match (e.g. vi2vi
    # GmbH versus vi2vi Retail Solution GmbH after a merger).
    # A company profile/news page can name the company and its address perfectly.
    # Require a company-brand domain as ownership evidence rather than approving
    # an unrelated directory or investor site from its title alone.
    return bool(name_match and locality and (address or len(distinctive) > 1) and
                brand and bool(distinctive & title))


class OwnerDocument(Document):
    """Keep the current page body separate from navigation and historical footers."""
    def __init__(self):
        super().__init__()
        self.excluded = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        excluded = (tag in {'footer', 'nav', 'aside'} or
                    attributes.get('role') in {'contentinfo', 'navigation'} or
                    bool(re.search(r'(?:^|[\s_-])(?:footer|navigation)(?:$|[\s_-])',
                                   attributes.get('class', '') + ' ' + attributes.get('id', ''), re.I)))
        # Void elements have no closing tag; only containers can hide a section.
        if tag in {'div', 'section', 'footer', 'nav', 'aside'}:
            self.excluded.append((tag, excluded))
            if excluded:
                self.hidden += 1
        super().handle_starttag(tag, attrs)
        if tag in {'h3', 'h4', 'h5', 'h6'}:
            self.in_heading = True

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag in {'h3', 'h4', 'h5', 'h6'}:
            self.in_heading = False
        if self.excluded and self.excluded[-1][0] == tag:
            _, excluded = self.excluded.pop()
            if excluded:
                self.hidden = max(0, self.hidden - 1)


IMPRINT = re.compile(r'impressum|imprint|legal.notice', re.I)
DIRECTORY = re.compile(r'firma eintragen|unternehmen eintragen|firmenverzeichnis|branchenbuch|'
                       r'ist das dein unternehmen|business directory|company directory', re.I)
LEGAL_FORMS = {'gmbh', 'ag', 'kg', 'ug', 'gbr', 'ohg'}
# Industry words cannot establish a distinctive owned domain (metallbau → metallbauer.io).
INDUSTRY_WORDS = GENERIC_NAME | {'metallbau', 'elektrotechnik', 'zimmerei', 'holzbau',
    'transporte', 'malerbetrieb', 'maler', 'stuckateurbetrieb', 'logistik', 'service',
    'digital', 'network', 'versicherung', 'versicherungen', 'unternehmen', 'werkstatt',
    'gastronomie', 'und'}


def _primary_name(candidate: dict) -> str:
    return re.split(r'\s+[-–—]\s+', candidate['name'], maxsplit=1)[0]


def _legal_company_name(candidate: dict) -> str:
    words = phrase(_primary_name(candidate)).split()
    forms = [index for index, word in enumerate(words) if word in LEGAL_FORMS]
    # OSM labels sometimes append a partner or branch after the legal entity.
    return ' '.join(words[:forms[-1]+1] if forms else words)


def _legal_match(candidate: dict, text: str) -> bool:
    name = _legal_company_name(candidate)
    return not (set(name.split()) & LEGAL_FORMS) or (' ' + name + ' ') in (' ' + phrase(text) + ' ')


def _brand_tokens(candidate: dict) -> set[str]:
    acronyms = {phrase(t) for t in re.findall(r'\b[A-ZÄÖÜ]{3,}\b', _primary_name(candidate))}
    return {t for t in tokens(_primary_name(candidate)) - INDUSTRY_WORDS - LEGAL_FORMS
            if len(t) >= 4 or (len(t) >= 3 and (any(c.isdigit() for c in t) or t in acronyms))}


def _compact_brand(candidate: dict, url: str) -> bool:
    domain = ''.join(phrase(host(url)).split())
    variants = tokens(_primary_name(candidate).casefold().replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue'))
    brands = _brand_tokens(candidate)
    return any(t in domain for t in brands | (variants - INDUSTRY_WORDS - tokens(_primary_name(candidate)))
               if len(t) >= 4 or (len(t) >= 3 and any(c.isdigit() for c in t))) or bool(
                   brands & tokens(host(url)))


def _phone_key(value: str) -> str:
    digits = re.sub(r'\D', '', value)
    if value.lstrip().startswith('+49'):
        digits = '0' + digits[2:].lstrip('0')
    elif digits.startswith('0049'):
        digits = '0' + digits[4:].lstrip('0')
    return digits if len(digits) >= 7 else ''


def _source_contact_matches(candidate: dict, doc: Document) -> bool:
    tags = candidate.get('tags') or {}
    text = ' '.join(doc.text)
    visible = tokens(text)
    city = tokens(str(tags.get('addr:city') or ''))
    street = tokens(str(tags.get('addr:street') or ''))
    postcode = str(tags.get('addr:postcode') or '')
    address = bool(city and city <= visible and
                   ((postcode and postcode in visible and (not street or street <= visible)) or
                    (not postcode and street and street <= visible)))
    email_pattern = r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}'
    page_emails = {e.casefold() for e in re.findall(email_pattern, text)}
    source_emails = {e.casefold() for k in ('email', 'contact:email')
                     for e in re.findall(email_pattern, str(tags.get(k) or ''))}
    # Compare complete phone spans. Joining every digit on a page invents matches.
    page_phones = {_phone_key(p) for p in re.findall(r'(?<!\w)(?:\+\s*)?\d[\d ()/.-]{5,}\d(?!\w)', text)} - {''}
    source_phones = {_phone_key(str(tags.get(k) or '')) for k in ('phone', 'contact:phone')} - {''}
    return address or bool(page_emails & source_emails) or bool(page_phones & source_phones)


def _owner_identity(candidate: dict, doc: Document, url: str, identity_headings: str) -> bool:
    text = ' '.join(doc.text)
    name = tokens(_legal_company_name(candidate))
    brands = _brand_tokens(candidate)
    if (not brands or not name <= tokens(text) or not _legal_match(candidate, text) or
            PARKED.search(text) or DIRECTORY.search(text)):
        return False
    return bool(_compact_brand(candidate, url) and brands & tokens(identity_headings) and
                _source_contact_matches(candidate, doc))


def verify_lead(candidate: dict, url: str, client, method: str) -> dict:
    """Inspect up to four same-host pages; prefer current imprint/contact evidence."""
    result = {'source_id': candidate['source_id'], 'lead_url': url, 'method': method,
              'accepted': False, 'reason': 'identity_not_verified'}
    if not public_url(url):
        return {**result, 'reason': 'non_company_url'}
    pending = [(0, url, bool(IMPRINT.search(urlsplit(url).path)))]
    visited = set()
    documents = []
    destination_host = None
    imprint_linked = bool(IMPRINT.search(urlsplit(url).path))
    for _ in range(4):
        if not pending:
            break
        pending.sort(key=lambda entry: entry[0])
        _, target, is_imprint = pending.pop(0)
        visited.add(target)
        record, body = client.get(target)
        final_url = public_url(record.get('final_url') or target)
        if record.get('state') != 'ok' or not final_url:
            result['reason'] = record.get('state') or 'unsafe_destination'
            continue
        if destination_host and host(final_url) != destination_host:
            result['reason'] = 'different_owner_host'
            continue
        if 'html' not in record.get('content_type', '').casefold():
            result['reason'] = 'not_html'
            continue
        destination_host = host(final_url)
        visited.add(final_url)
        source = body.decode('utf-8', errors='replace')
        doc = Document(); doc.feed(source)
        owner = OwnerDocument(); owner.feed(source)
        is_imprint = is_imprint or bool(IMPRINT.search(urlsplit(final_url).path))
        if is_imprint:
            # Focus on the provider declaration, before boilerplate/credits that
            # can retain old companies even outside a semantic <footer> element.
            body_text = ' '.join(owner.text)
            for title in owner.title:
                body_text = body_text.replace(title, '', 1)
            declaration = re.split(r'Haftung(?:sausschluss)?|Urheberrecht|Quellenangaben|'
                                   r'Konzept\s*(?:&|und)\s*Gestaltung',
                                   body_text, maxsplit=1, flags=re.I)[0]
            for heading in owner.headings:
                declaration = declaration.replace(heading, '', 1)
            owner.text = [declaration]
        documents.append((final_url, doc, owner, is_imprint))
        for href, label in doc.links:
            contact = public_url(clean_url(final_url, href))
            if not contact or host(contact) != destination_host or contact in visited:
                continue
            clue = label + ' ' + urlsplit(contact).path
            if not CONTACT.search(clue):
                continue
            imprint = bool(IMPRINT.search(clue))
            imprint_linked |= imprint
            priority = 1 if imprint else 3 if re.search(r'kontakt|contact', clue, re.I) else 4
            if not any(entry[1] == contact for entry in pending):
                pending.append((priority, contact, imprint))
        home = root(final_url)
        if home not in visited and not any(entry[1] == home for entry in pending):
            pending.append((2, home, False))
    legal = bool(set(phrase(_primary_name(candidate)).split()) & LEGAL_FORMS)
    imprints = [entry for entry in documents if entry[3]]
    if legal and imprint_linked:
        # An accessible, consistent current owner is required once an imprint is known.
        if not imprints or any(not _legal_match(candidate, ' '.join(owner.text)) for _,_,owner,_ in imprints):
            return {**result, 'reason': 'owner_not_verified'}
    headings = ' '.join(' '.join(doc.title + doc.h1) for _,doc,_,_ in documents)
    # Imprint/contact proof comes first so persisted evidence points to ownership.
    for final_url, doc, owner, is_imprint in sorted(documents, key=lambda d: not d[3]):
        if DIRECTORY.search(' '.join(doc.text)):
            continue
        if (_identity(candidate, owner, final_url, method, url) or
                _owner_identity(candidate, owner, final_url, headings)):
            return {**result, 'accepted': True, 'reason': 'verified_identity',
                    'website_url': root(final_url), 'evidence_url': final_url}
    return result


def _pace_search(delay: float) -> None:
    global _LAST_SEARCH
    with _SEARCH_LOCK:
        wait = delay - (time.monotonic() - _LAST_SEARCH)
        if wait > 0:
            time.sleep(wait)
        _LAST_SEARCH = time.monotonic()


class BraveSearch:
    """Documented search API; credentials are sent only to its fixed HTTPS endpoint."""
    def __init__(self, api_key: str, *, opener=None, delay: float = 1.0):
        self.api_key = api_key
        self.opener = opener or build_opener(NoRedirect).open
        self.delay = max(1.0, delay)
        self.disabled = False

    def __call__(self, query: str) -> list[dict]:
        if self.disabled:
            return []
        _pace_search(self.delay)
        request = Request('https://api.search.brave.com/res/v1/web/search?' + urlencode({
            'q': query[:600], 'country': 'DE', 'search_lang': 'de', 'count': 5,
            'text_decorations': 'false'}), headers={
                'Accept': 'application/json', 'X-Subscription-Token': self.api_key,
                'User-Agent': 'HiringScraper/0.2 (company homepage discovery)'})
        try:
            with self.opener(request, timeout=10) as response:
                payload = json.loads(response.read(1_000_000))
            results = (payload.get('web') or {}).get('results') or []
            return [r for r in results[:5] if isinstance(r, dict) and isinstance(r.get('url'), str)]
        except Exception:
            # Stop this run on rate limits/auth/network errors; do not repeatedly spend requests.
            self.disabled = True
            LOG.warning('Company website search unavailable; remaining map/email results retained')
            return []


class TavilySearch:
    """Basic Tavily search: URL leads only, one credit per successful basic request."""
    def __init__(self, api_key: str, *, opener=None, delay: float = 1.0):
        self.api_key = api_key
        self.opener = opener or build_opener(NoRedirect).open
        self.delay = max(1.0, delay)
        self.disabled = False
        self.credits_used = 0

    def __call__(self, query: str) -> list[dict]:
        if self.disabled:
            return []
        _pace_search(self.delay)
        request = Request('https://api.tavily.com/search', method='POST',
                          data=json.dumps({
                              'query': query[:600], 'topic': 'general', 'country': 'germany',
                              'search_depth': 'basic', 'auto_parameters': False, 'max_results': 5,
                              'include_answer': False, 'include_raw_content': False,
                              'include_images': False, 'include_usage': True,
                          }).encode('utf-8'), headers={
                              'Authorization': 'Bearer ' + self.api_key,
                              'Content-Type': 'application/json', 'Accept': 'application/json',
                              'User-Agent': 'HiringScraper/0.2 (company homepage discovery)'})
        try:
            with self.opener(request, timeout=10) as response:
                payload = json.loads(response.read(1_000_000))
            results = payload.get('results', [])
            if not isinstance(results, list):
                raise ValueError('Invalid search results')
            credits = (payload.get('usage') or {}).get('credits', 1)
            self.credits_used += max(0, int(credits))
            return [{'url': r['url'], 'title': str(r.get('title') or '')}
                    for r in results if isinstance(r, dict) and isinstance(r.get('url'), str)][:5]
        except Exception:
            self.disabled = True
            # Provider errors can echo request data; never log bodies, headers or tokens.
            LOG.warning('Tavily company website search unavailable; remaining map/email results retained')
            return []


def configured_website_search():
    """Prefer the configured Tavily key; otherwise retain optional Brave support."""
    tavily_key = os.getenv('TAVILY_API_KEY', '').strip()
    if tavily_key:
        return TavilySearch(tavily_key)
    brave_key = os.getenv('BRAVE_SEARCH_API_KEY', '').strip()
    return BraveSearch(brave_key) if brave_key else None


def _search_queries(candidate: dict, search_area_hint: str | None) -> list[tuple[str, str]]:
    tags = candidate.get('tags') or {}
    locality = ' '.join(str(tags[k]) for k in ('addr:postcode', 'addr:city') if tags.get(k))
    locality = locality or str(search_area_hint or '').strip()
    fallback_locality = str(tags.get('addr:city') or tags.get('addr:postcode') or search_area_hint or '').strip()
    full = candidate['name'].replace('"', ' ')
    simple = re.sub(r'\b(?:gmbh|ag|kg|mbh|ug|gbr|ohg|haftungsbeschränkt|limited|ltd|inc)\b',
                    ' ', _primary_name(candidate), flags=re.I)
    simple = ' '.join(simple.split())
    return [('exact', f'"{full}" {locality} Unternehmen Website'.strip()),
            ('contact_fallback', f'{simple} {fallback_locality} Kontakt Impressum'.strip()),
            ('official_fallback', f'{full} {fallback_locality} offizielle Website Kontakt'.strip())]


def _crawl_exhausted(client) -> bool:
    budget = getattr(client, 'request_budget', None)
    if budget is not None and budget.used >= budget.limit:
        return True
    maximum = getattr(client, 'max_requests', None)
    return maximum is not None and len(client.records) >= maximum


def discover_missing_websites(candidates: list[dict], client, *, search=None,
                              max_searches: int = 50, max_companies: int = 100,
                              max_candidates: int = 3, search_area_hint: str | None = None,
                              progress_callback=None) -> list[dict]:
    """Fill missing homepages with email, then fair bounded passes of basic queries.

    Regional hints generate leads only; source tags and own-page identity verify them.
    """
    decisions = []
    missing = [r for r in candidates if not r.get('website_url')]
    missing.sort(key=lambda r: (not bool((r.get('tags') or {}).get('email') or
                                        (r.get('tags') or {}).get('contact:email')), r['source_id']))
    contexts = [{'candidate':r, 'checked':{}, 'verified':{}, 'ambiguous':False}
                for r in missing[:max(0, max_companies)]]

    def report():
        if progress_callback:
            progress_callback('Searching and verifying missing company websites',
                              companies_found=len(candidates),
                              homepages_found=sum(bool(r.get('website_url')) for r in candidates))

    def check(context, leads, method, strategy=None, query=None):
        candidate = context['candidate']
        seen = set()
        checked = 0
        for lead in leads:
            url = public_url(lead)
            if not url or host(url) in seen:
                continue
            seen.add(host(url))
            if checked >= max(0, max_candidates):
                break
            checked += 1
            cache_key = (method, url)
            if cache_key in context['checked']:
                continue
            try:
                decision = verify_lead(candidate, url, client, method)
            except Exception:
                decision = {'source_id':candidate['source_id'], 'lead_url':url,
                            'method':method, 'accepted':False, 'reason':'verification_failed'}
            if query is not None:
                decision.update(query=query, query_strategy=strategy)
            context['checked'][cache_key] = decision
            decisions.append(decision)
            if decision['accepted']:
                context['verified'][host(decision['website_url'])] = decision
        verified = context['verified']
        if len(verified) == 1:
            chosen = next(iter(verified.values()))
            suffix = chosen.get('query_strategy')
            match_method = ('osm_email_verified_page' if chosen['method']=='email' else
                            'web_search_verified_' + suffix if suffix in {'contact_fallback','official_fallback'}
                            else 'web_search_verified_page')
            candidate.update(website_url=chosen['website_url'], domain=host(chosen['website_url']),
                             domain_match_method=match_method, domain_evidence_url=chosen['evidence_url'])
        elif len(verified) > 1:
            context['ambiguous'] = True
            for decision in verified.values():
                decision.update(accepted=False, reason='ambiguous_domains')
        report()

    # Complete cheap source-email verification before spending on search.
    for context in contexts:
        candidate = context['candidate']
        tags = candidate.get('tags') or {}
        email_rows = resolve_osm_email_websites([{**candidate, 'website':None,
                                                'email':tags.get('email'),
                                                'contact:email':tags.get('contact:email')}])
        emails = [r['website_url'] for r in email_rows if r.get('ineligible_reason') not in
                  {'free_email_provider','non_public_email_domain'}]
        check(context, emails, 'email')
    searches = 0
    query_cache = {}
    unavailable = False
    # First queries across companies precede contact and official-website retries.
    for stage in range(3):
        # Reserve a third of available calls for later strategies, otherwise a
        # large batch consumes the entire cap before any fallback can run.
        remaining = max(0, max_searches - searches)
        allowance = remaining if stage == 2 else max(1, remaining * 2 // 3)
        stage_limit = min(max(0, max_searches), searches + allowance)
        for context in contexts:
            candidate = context['candidate']
            if (candidate.get('website_url') or context['ambiguous'] or not search or unavailable or
                    getattr(search, 'disabled', False) or _crawl_exhausted(client) or not _brand_tokens(candidate)):
                continue
            strategy, query = _search_queries(candidate, search_area_hint)[stage]
            if query not in query_cache:
                if searches >= stage_limit:
                    continue
                searches += 1
                try:
                    hits = search(query)
                    query_cache[query] = hits if isinstance(hits, list) else []
                except Exception:
                    LOG.warning('Company website search failed; retaining unresolved candidate')
                    unavailable = True
                    continue
            leads = [hit.get('url') for hit in query_cache[query] if isinstance(hit, dict)]
            check(context, leads, 'search', strategy, query)
    return decisions
