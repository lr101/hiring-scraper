"""High-signal website leads from OSM Wikidata references."""
from __future__ import annotations

import json
import ipaddress
import re
import socket
import unicodedata
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


_REFERENCE_TAGS = {
    "wikidata": "entity",
    "brand:wikidata": "brand",
    "operator:wikidata": "operator",
}
_QID = re.compile(r"^Q[1-9][0-9]*$")
_NAME_NOISE = {
    "ag", "gbr", "gesellschaft", "gmbh", "haftungsbeschraenkt", "holding", "inc",
    "kg", "mbh", "limited", "ltd", "company", "group", "deutschland", "germany",
    "karlsruhe", "baden", "wuerttemberg", "badenwuerttemberg", "office", "standort",
    "com", "de", "org", "net", "eu", "www", "info", "co",
}
_GENERIC_TLDS = {"com", "org", "net", "eu", "info", "biz", "gov", "edu", "int"}
_FREE_EMAIL_DOMAINS = {
    "aol.com", "gmail.com", "gmx.de", "gmx.net", "googlemail.com", "hotmail.com", "live.com",
    "msn.com", "outlook.com", "t-online.de", "web.de", "yahoo.com", "yahoo.de",
}
_EMAIL = re.compile(r"(?<![\w.+-])[^\s@,;<>]+@([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)(?![\w.-])", re.IGNORECASE)
_EMAIL_DOMAIN_LITERAL = re.compile(r"(?<![\w.+-])[^\s@,;<>]+@\[(?:ipv6:)?([0-9a-f:.]+)\]",
                                    re.IGNORECASE)
_NON_PUBLIC_HOST_SUFFIXES = (".internal", ".invalid", ".local", ".localhost", ".test", ".example",
                             ".home.arpa", ".example.com", ".example.net", ".example.org")
_NON_PUBLIC_HOSTS = {"localhost", "localhost.localdomain", "localdomain", "ip6-localhost", "ip6-loopback",
                     "example", "example.com", "example.net", "example.org"}


def _source_id(element: dict) -> str:
    return f"{element.get('type')}/{element.get('id')}"


def _web_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if not parsed.scheme:
            parsed = urlsplit("https://" + value)
            value = "https://" + value
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not hostname or username or password:
        return None
    return value


def _claim_values(entity: dict, property_id: str) -> list[tuple[str, str, bool]]:
    statements = entity.get("claims", {}).get(property_id, [])
    active = [statement for statement in statements if statement.get("rank") != "deprecated"]
    active.sort(key=lambda statement: 0 if statement.get("rank") == "preferred" else 1)
    values = []
    for statement in active:
        snak = statement.get("mainsnak") or {}
        data = snak.get("datavalue") or {}
        value = _web_url(data.get("value"))
        if value and all(existing[0] != value for existing in values):
            has_deprecation_reason = bool(statement.get("qualifiers", {}).get("P2241"))
            values.append((value, statement.get("rank", "normal"), has_deprecation_reason))
    return values


def _entity_names(entity: dict, qid: str) -> list[str]:
    names = [qid]
    for language in ("de", "en"):
        label = (entity.get("labels", {}).get(language) or {}).get("value")
        if label and label not in names:
            names.append(label)
        for alias in entity.get("aliases", {}).get(language, []):
            value = alias.get("value") if isinstance(alias, dict) else None
            if value and value not in names:
                names.append(value)
    return names


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value or "").casefold()
    tokens = set()
    for raw in re.findall(r"[^\W_]+", normalized, re.UNICODE):
        token = "".join(char for char in raw if char.isalnum())
        if token and token not in _NAME_NOISE and len(token) > 1:
            tokens.add(token)
    return tokens


def _domain_tokens(url: str) -> set[str]:
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    return set().union(*(_tokens(part) for part in host.split("."))) if host else set()


def _domain(url: object) -> str | None:
    safe_url = _web_url(url)
    return (urlsplit(safe_url).hostname or "").casefold().removeprefix("www.") if safe_url else None


def _hostname(url: object) -> str | None:
    safe_url = _web_url(url)
    return (urlsplit(safe_url).hostname or "").casefold() if safe_url else None


def _email_domains(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    domains = []
    for match in _EMAIL.finditer(value):
        domain = match.group(1).casefold().rstrip(".")
        if domain not in domains:
            domains.append(domain)
    for match in _EMAIL_DOMAIN_LITERAL.finditer(value):
        try:
            domain = ipaddress.IPv6Address(match.group(1)).compressed.casefold()
        except ipaddress.AddressValueError:
            continue
        if domain not in domains:
            domains.append(domain)
    return domains


def _email_website_url(domain: str) -> str:
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return f"https://{domain}/"
    return f"https://[{domain}]/" if ":" in domain else f"https://{domain}/"


def _is_public_hostname(hostname: str | None) -> bool:
    """Reject literal, local and IANA-reserved names before enrichment."""
    host = (hostname or "").casefold().rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    try:
        socket.inet_aton(host)
        return False
    except OSError:
        pass
    return host not in _NON_PUBLIC_HOSTS and not host.endswith(_NON_PUBLIC_HOST_SUFFIXES)


def _email_verifications(items: object) -> dict[tuple[str, str], dict]:
    """Index source-keyed, auditable manual/public-page verification evidence."""
    if isinstance(items, dict):
        iterable = items.values()
    elif isinstance(items, list):
        iterable = items
    else:
        return {}
    indexed = {}
    for item in iterable:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        domain = _domain(item.get("website_url"))
        if source_id and domain:
            indexed[(source_id, domain)] = item
    return indexed


def _non_german_country_domain(url: str) -> bool:
    """Reject country-specific brand URLs that point outside the German market."""
    host = (urlsplit(url).hostname or "").casefold()
    tld = host.rsplit(".", 1)[-1]
    return len(tld) == 2 and tld not in {"de", "eu"} and tld not in _GENERIC_TLDS


def _identity_score(candidate_name: str, entity_names: list[str], website_url: str | None = None) -> float:
    candidate_tokens = _tokens(candidate_name)
    if not candidate_tokens:
        return 0.0
    score = 0.0
    for name in entity_names:
        entity_tokens = _tokens(name)
        if not entity_tokens:
            continue
        overlap = candidate_tokens & entity_tokens
        score = max(score, len(overlap) / min(len(candidate_tokens), len(entity_tokens)))
    if website_url:
        domain_tokens = _domain_tokens(website_url)
        overlap = candidate_tokens & domain_tokens
        if overlap:
            score = max(score, len(overlap) / min(len(candidate_tokens), len(domain_tokens)))
    return score


def _references(tags: dict) -> list[tuple[str, str]]:
    references = []
    for tag, relation in _REFERENCE_TAGS.items():
        raw = tags.get(tag, "")
        for value in re.split(r"[;,]", str(raw)):
            qid = value.strip()
            if _QID.fullmatch(qid) and (relation, qid) not in references:
                references.append((relation, qid))
    return references


def referenced_entity_ids(osm_response: dict) -> list[str]:
    """Return distinct Wikidata IDs referenced by matching OSM elements."""
    return sorted({qid for element in osm_response.get("elements", [])
                   for _, qid in _references(element.get("tags") or {})})


def fetch_wikidata_entities(qids: list[str], opener=None) -> dict:
    """Fetch labels, aliases and official-website claims in API-sized batches."""
    opener = opener or urlopen
    entities = {}
    unique_ids = sorted({qid for qid in qids if _QID.fullmatch(qid)})
    for offset in range(0, len(unique_ids), 50):
        batch = unique_ids[offset:offset + 50]
        query = urlencode({
            "action": "wbgetentities", "ids": "|".join(batch),
            "props": "labels|aliases|claims", "languages": "de|en", "format": "json",
        })
        request = Request(
            "https://www.wikidata.org/w/api.php?" + query,
            headers={"User-Agent": "HiringScraper/0.2 (public research project)",
                     "Accept": "application/json"},
        )
        with opener(request, timeout=30) as response:
            payload = response.read()
        entities.update(json.loads(payload).get("entities", {}))
    return entities


def resolve_osm_websites(candidates: list[dict], osm_response: dict, entities: dict) -> list[dict]:
    """Resolve OSM candidates into auditable official-website suggestions.

    Direct ``wikidata`` references are treated as entity-level evidence. Brand
    and operator links remain suggestions unless the referenced entity label or
    alias overlaps the mapped company name, which avoids assigning a chain's
    corporate site to a different local franchise or organization.
    """
    elements = {_source_id(element): element for element in osm_response.get("elements", [])}
    candidate_by_id = {str(candidate.get("source_id") or
                           f"{candidate.get('osm_type')}/{candidate.get('osm_id')}"): candidate
                       for candidate in candidates}
    mapped_domains = {_domain(candidate.get("website")) for candidate in candidates
                      if candidate.get("website")}
    mapped_domains.discard(None)
    output = []
    for source_id, candidate in candidate_by_id.items():
        if (candidate.get("website") or "").strip():
            continue
        element = elements.get(source_id)
        if not element:
            continue
        tags = element.get("tags") or {}
        for relation, qid in _references(tags):
            entity = entities.get(qid)
            if not entity:
                continue
            names = _entity_names(entity, qid)
            for website_url, claim_rank, has_deprecation_reason in _claim_values(entity, "P856"):
                score = _identity_score(candidate.get("name", ""), names, website_url)
                match = relation == "entity" or score >= 0.5
                already_mapped_domain = _domain(website_url) in mapped_domains
                foreign_brand_site = relation != "entity" and _non_german_country_domain(website_url)
                eligible = bool(match and not already_mapped_domain and not has_deprecation_reason and
                                not foreign_brand_site)
                row = {
                    "source_id": source_id,
                    "osm_source_url": f"https://www.openstreetmap.org/{source_id}",
                    "company_name": candidate.get("name", ""),
                    "entity_id": qid,
                    "entity_name": next((name for name in names if name != qid), qid),
                    "relation": relation,
                    "website_url": website_url,
                    "website_claim_rank": claim_rank,
                    "identity_score": round(score, 3),
                    "eligible_for_enrichment": eligible,
                    "evidence_url": f"https://www.wikidata.org/wiki/{qid}",
                    "method": f"osm_{relation}_wikidata_p856",
                }
                if already_mapped_domain:
                    row["ineligible_reason"] = ("operator_domain_already_mapped_in_osm" if relation == "operator"
                                                 else "website_domain_already_mapped_in_osm")
                elif has_deprecation_reason:
                    row["ineligible_reason"] = "wikidata_website_has_deprecation_qualifier"
                elif foreign_brand_site:
                    row["ineligible_reason"] = "non_german_country_domain"
                output.append(row)
        osm_url = _web_url(tags.get("url"))
        if osm_url:
            score = _identity_score(candidate.get("name", ""), [], osm_url)
            output.append({
                "source_id": source_id,
                "osm_source_url": f"https://www.openstreetmap.org/{source_id}",
                "company_name": candidate.get("name", ""),
                "entity_id": None,
                "entity_name": None,
                "relation": "osm_url_tag",
                "website_url": osm_url,
                "identity_score": round(score, 3),
                "eligible_for_enrichment": score >= 0.5,
                "evidence_url": f"https://www.openstreetmap.org/{source_id}",
                "method": "osm_url_name_match",
            })
    return output


def resolve_osm_email_websites(candidates: list[dict], verification_evidence: object = None) -> list[dict]:
    """Suggest homepage roots from direct OSM email tags.

    A non-free email domain is useful same-record evidence but does not itself
    prove that the domain serves a public homepage.  Automatic enrichment is
    therefore limited to a complete candidate/domain token match *and* a
    source-keyed, public-page identity verification. Every other email-derived
    value is retained as a reviewable suggestion with an explicit reason.
    """
    mapped_domains = {_domain(candidate.get("website")) for candidate in candidates
                      if candidate.get("website")}
    mapped_domains.discard(None)
    verifications = _email_verifications(verification_evidence)
    output = []
    for candidate in candidates:
        if (candidate.get("website") or "").strip():
            continue
        source_id = str(candidate.get("source_id") or
                        f"{candidate.get('osm_type')}/{candidate.get('osm_id')}")
        email_values = (candidate.get("email"), candidate.get("contact:email"))
        domains = []
        for value in email_values:
            for domain in _email_domains(value):
                if domain not in domains:
                    domains.append(domain)
        for domain in domains:
            website_url = _email_website_url(domain)
            score = _identity_score(candidate.get("name", ""), [], website_url)
            free_provider = domain in _FREE_EMAIL_DOMAINS
            already_mapped_domain = domain in mapped_domains
            public_domain = _is_public_hostname(_hostname(website_url))
            verification = verifications.get((source_id, domain), {})
            verified = bool(verification.get("identity_confirmed") is True and
                            _web_url(verification.get("evidence_url")) and
                            _is_public_hostname(_hostname(verification.get("evidence_url"))))
            verification_state = ("identity_confirmed" if verified else
                                  str(verification.get("access_state") or "not_checked"))
            eligible = bool(score >= 0.8 and public_domain and not free_provider and
                            not already_mapped_domain and verified)
            row = {
                "source_id": source_id,
                "osm_source_url": f"https://www.openstreetmap.org/{source_id}",
                "company_name": candidate.get("name", ""),
                "entity_id": None,
                "entity_name": None,
                "relation": "osm_email_tag",
                "website_url": website_url,
                "identity_score": round(score, 3),
                "eligible_for_enrichment": eligible,
                "evidence_url": f"https://www.openstreetmap.org/{source_id}",
                "method": "osm_email_domain_name_match",
                "email_verification_state": verification_state,
                "verification_evidence_url": verification.get("evidence_url") or "",
            }
            if not public_domain:
                row["ineligible_reason"] = "non_public_email_domain"
            elif free_provider:
                row["ineligible_reason"] = "free_email_provider"
            elif already_mapped_domain:
                row["ineligible_reason"] = "website_domain_already_mapped_in_osm"
            elif score < 0.8:
                row["ineligible_reason"] = "email_domain_does_not_identify_candidate"
            elif not verified:
                row["ineligible_reason"] = "email_domain_identity_not_verified"
            output.append(row)
    return output


def select_website_enrichments(suggestions: list[dict]) -> dict[str, dict]:
    """Choose at most one eligible website for each OSM source, by evidence strength."""
    priority = {"entity": 0, "brand": 1, "operator": 2, "osm_url_tag": 3, "osm_email_tag": 4}
    selected = {}
    def is_eligible(row):
        value = row.get("eligible_for_enrichment")
        return value is True or str(value).casefold() == "true"

    def identity_score(row):
        try:
            return float(row.get("identity_score") or 0)
        except (TypeError, ValueError):
            return 0.0

    def locale_rank(row):
        host = (urlsplit(row.get("website_url", "")).hostname or "").casefold()
        if host.endswith(".de"):
            return 0
        if host.endswith((".com", ".org", ".eu")):
            return 1
        return 2

    def claim_rank(row):
        return 0 if row.get("website_claim_rank") == "preferred" else 1

    selected_domains = set()
    for item in sorted(suggestions, key=lambda row: (
            priority.get(row.get("relation"), 9), -identity_score(row),
            row.get("source_id", ""), locale_rank(row), claim_rank(row), row.get("website_url", ""))):
        if is_eligible(item) and item.get("website_url"):
            domain = _domain(item.get("website_url"))
            if item["source_id"] in selected or domain in selected_domains:
                continue
            selected[item["source_id"]] = item
            if domain:
                selected_domains.add(domain)
    return selected
