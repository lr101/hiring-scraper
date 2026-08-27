from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from django.conf import settings

from .network import UnsafeNetworkAddress, validate_public_hostname
from .structured_data import iter_json_ld_objects


class CompanyDiscoveryError(RuntimeError):
    """The submitted domain could not be turned into a usable company source."""


@dataclass(frozen=True, slots=True)
class DiscoveredCompany:
    domain: str
    name: str
    website_url: str
    career_url: str
    source_kind: str = "json_ld"
    allowed_hosts: tuple[str, ...] = ()


class _SiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta: dict[str, str] = {}
        self.canonical_url = ""
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_anchor = False
        self._anchor_href = ""
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if normalized_tag == "title":
            self._in_title = True
            self._title_parts = []
        elif normalized_tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            content = attributes.get("content", "").strip()
            if key and content:
                self.meta[key.casefold()] = content
        elif normalized_tag == "link" and "canonical" in {
            part.casefold() for part in attributes.get("rel", "").split()
        }:
            self.canonical_url = attributes.get("href", "").strip()
        elif normalized_tag == "a":
            self._in_anchor = True
            self._anchor_href = attributes.get("href", "").strip()
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_anchor:
            self._anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "title" and self._in_title:
            self.title = " ".join(" ".join(self._title_parts).split())
            self._in_title = False
        elif normalized_tag == "a" and self._in_anchor:
            self.links.append((self._anchor_href, " ".join(" ".join(self._anchor_parts).split())))
            self._in_anchor = False


def normalize_domain(value: str) -> str:
    """Return a safe, lower-case hostname from a domain or pasted website URL."""
    raw_value = value.strip()
    if not raw_value:
        raise ValueError("Enter a company domain.")
    candidate = raw_value if "://" in raw_value else f"https://{raw_value}"
    parsed = urlsplit(candidate)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a domain such as example.com.")
    if parsed.username or parsed.password:
        raise ValueError("A domain cannot contain login credentials.")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Enter a valid company domain.") from None
    if port is not None:
        raise ValueError("Enter a domain without a port number.")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("Enter a valid company domain.")
    try:
        hostname = hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError:
        raise ValueError("Enter a valid company domain.") from None
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if not _valid_public_hostname(hostname):
        raise ValueError("Enter a public company domain.")
    return hostname


def discover_company(domain: str, *, client: httpx.Client | None = None) -> DiscoveredCompany:
    """Read a company's public website and find its name and career page."""
    normalized_domain = normalize_domain(domain)
    website_url, html = _fetch_website(normalized_domain, client=client)
    parser = _SiteParser()
    parser.feed(html)

    name = _organization_name(html) or parser.meta.get("og:site_name", "")
    name = name.strip() or _title_name(parser.title)
    name = name or _display_name_from_domain(normalized_domain)
    career_url = (
        _find_career_url(
            parser=parser,
            website_url=website_url,
            company_domain=normalized_domain,
        )
        or website_url
    )
    career_host = urlsplit(career_url).hostname or normalized_domain
    allowed_hosts = tuple(
        sorted({normalized_domain, f"www.{normalized_domain}", career_host.casefold()})
    )
    return DiscoveredCompany(
        domain=normalized_domain,
        name=name[:200],
        website_url=website_url,
        career_url=career_url,
        allowed_hosts=allowed_hosts,
    )


def _fetch_website(domain: str, *, client: httpx.Client | None) -> tuple[str, str]:
    owns_client = client is None
    http_client = client or httpx.Client(
        follow_redirects=False,
        timeout=float(getattr(settings, "COMPANY_DISCOVERY_TIMEOUT_SECONDS", 15)),
        headers={
            "User-Agent": getattr(
                settings,
                "COMPANY_DISCOVERY_USER_AGENT",
                "hiring-scraper/0.1 (self-hosted company discovery)",
            )
        },
    )
    current_url = f"https://{domain}/"
    try:
        for _ in range(6):
            host = urlsplit(current_url).hostname
            if host is None:
                raise CompanyDiscoveryError("The company website URL is invalid.")
            validate_public_hostname(host)
            response = http_client.get(current_url, follow_redirects=False)
            if response.is_redirect:
                location = response.headers.get("location", "")
                next_url = _safe_https_url(urljoin(current_url, location))
                if not next_url or not _same_site(next_url, domain):
                    raise CompanyDiscoveryError(
                        "The company website redirected to an unsafe domain."
                    )
                current_url = next_url
                continue
            response.raise_for_status()
            return str(response.url or current_url), response.text
        raise CompanyDiscoveryError("The company website redirected too many times.")
    except CompanyDiscoveryError:
        raise
    except UnsafeNetworkAddress as error:
        raise CompanyDiscoveryError(
            "That company domain does not resolve to a public address."
        ) from error
    except (httpx.HTTPError, UnicodeError) as error:
        raise CompanyDiscoveryError(
            "Could not reach that company website. Check the domain and try again."
        ) from error
    finally:
        if owns_client:
            http_client.close()


def _organization_name(html: str) -> str:
    organization_types = {
        "corporation",
        "educationalorganization",
        "governmentorganization",
        "localbusiness",
        "ngo",
        "organization",
    }
    for item in iter_json_ld_objects(html):
        item_types = item.get("@type")
        types = item_types if isinstance(item_types, list) else [item_types]
        normalized_types = {value.casefold() for value in types if isinstance(value, str)}
        if normalized_types & organization_types:
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return ""


def _find_career_url(*, parser: _SiteParser, website_url: str, company_domain: str) -> str:
    candidates: list[tuple[int, int, str]] = []
    for index, (href, text) in enumerate(parser.links):
        candidate = _safe_https_url(urljoin(website_url, href))
        if not candidate:
            continue
        parsed = urlsplit(candidate)
        haystack = f"{href} {text}".casefold()
        matched_terms = sum(
            term in haystack
            for term in (
                "career",
                "careers",
                "job",
                "jobs",
                "vacanc",
                "position",
                "opening",
                "karriere",
                "stellen",
            )
        )
        if not matched_terms:
            continue
        score = matched_terms * 10
        if _same_site(candidate, company_domain):
            score += 5
        if any(term in parsed.path.casefold() for term in ("career", "job", "karriere", "stellen")):
            score += 3
        candidates.append((score, -index, candidate))
    return max(candidates, default=(0, 0, ""))[2]


def _title_name(title: str) -> str:
    if not title:
        return ""
    pieces = re.split(r"\s*[|·]\s*|\s+-\s+", title, maxsplit=1)
    return pieces[0].strip()


def _display_name_from_domain(domain: str) -> str:
    label = domain.split(".", 1)[0].replace("-", " ").replace("_", " ")
    return " ".join(part.capitalize() for part in label.split())


def _safe_https_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None:
        return ""
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError:
        return ""
    if not _valid_public_hostname(hostname):
        return ""
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


def _same_site(value: str, domain: str) -> bool:
    hostname = urlsplit(value).hostname
    if hostname is None:
        return False
    hostname = hostname.casefold().rstrip(".")
    normalized_domain = normalize_domain(domain)
    return hostname == normalized_domain or hostname.endswith(f".{normalized_domain}")


def _valid_public_hostname(hostname: str) -> bool:
    if not hostname or len(hostname) > 253 or "." not in hostname:
        return False
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
        (".local", ".internal", ".lan", ".home.arpa")
    ):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        return False
    return all(
        len(label) <= 63
        and label
        and not label.startswith("-")
        and not label.endswith("-")
        and re.fullmatch(r"[a-z0-9-]+", label) is not None
        for label in hostname.split(".")
    )


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
