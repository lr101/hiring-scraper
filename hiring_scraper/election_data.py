"""Municipality-level official election results used as optional job context."""
from __future__ import annotations

import csv
from functools import lru_cache
from importlib.resources import files
import re
import unicodedata


ELECTION_NAME = "Bundestagswahl 2025"
ELECTION_SOURCE = "Die Bundeswahlleiterin"
ELECTION_SOURCE_URL = (
    "https://www.bundeswahlleiterin.de/mitteilungen/bundestagswahlen/2025/"
    "20250430_wbz_btw25.html"
)
_STATE_ALIASES = {
    "baden wurttemberg": "Baden-Württemberg", "bayern": "Bayern",
    "berlin": "Berlin", "brandenburg": "Brandenburg", "bremen": "Bremen",
    "hamburg": "Hamburg", "hessen": "Hessen", "mecklenburg vorpommern": "Mecklenburg-Vorpommern",
    "niedersachsen": "Niedersachsen", "nordrhein westfalen": "Nordrhein-Westfalen",
    "rheinland pfalz": "Rheinland-Pfalz", "saarland": "Saarland", "sachsen": "Sachsen",
    "sachsen anhalt": "Sachsen-Anhalt", "schleswig holstein": "Schleswig-Holstein",
    "thuringen": "Thüringen",
}
_ENGLISH_CITY_ALIASES = {
    "munich": "München", "cologne": "Köln", "nuremberg": "Nürnberg",
    "frankfurt main": "Frankfurt am Main", "frankfurt am main": "Frankfurt am Main",
}
_GERMANY_LABELS = {"de", "deu", "germany", "deutschland", "allemagne", "duitsland"}
# ISO 3166-1 alpha-2 country codes. A two-letter component such as "BE" in
# "Essen, BE" is enough to show that the location is outside Germany.
_FOREIGN_COUNTRY_CODES = set((
    "ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd bf bg bh bi bj "
    "bl bm bn bo bq br bs bt bv bw by bz ca cc cd cf cg ch ci ck cl cm cn co "
    "cr cu cv cw cx cy cz dj dk dm do dz ec ee eg eh er es et fi fj fk fm fo "
    "fr ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr "
    "ht hu id ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn kp kr "
    "kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mf mg mh mk ml mm "
    "mn mo mp mq mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no np nr nu "
    "nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb "
    "sc sd se sg sh si sj sk sl sm sn so sr ss st sv sx sy sz tc td tf tg th "
    "tj tk tl tm tn to tr tt tv tw tz ua ug um us uy uz va vc ve vg vi vn vu "
    "wf ws ye yt za zm zw uk"
).split()) - {"de"}
_FOREIGN_COUNTRY_NAMES = {
    "afghanistan", "albania", "algeria", "andorra", "angola", "argentina",
    "armenia", "australia", "austria", "osterreich", "azerbaijan", "bahrain",
    "bangladesh", "belarus", "belgium", "belgien", "belgique", "belgie",
    "belize", "benin", "bhutan", "bolivia", "bosnia and herzegovina",
    "botswana", "brazil", "brasil", "brunei", "bulgaria", "burkina faso",
    "cambodia", "cameroon", "canada", "chile", "china", "colombia", "congo",
    "costa rica", "croatia", "cyprus", "czech republic", "czechia", "tschechien",
    "denmark", "danemark", "danmark", "dominican republic", "ecuador", "egypt",
    "estonia", "estland", "ethiopia", "finland", "finnland", "france",
    "frankreich", "georgia", "germany", "ghana", "greece", "griechenland",
    "hungary", "ungarn", "iceland", "india", "indonesia", "iran", "iraq",
    "ireland", "irland", "israel", "italy", "italien", "japan", "jordan",
    "kazakhstan", "kenya", "kosovo", "kuwait", "latvia", "lettland", "lebanon",
    "libya", "liechtenstein", "lithuania", "litauen", "luxembourg", "luxemburg",
    "malaysia", "malta", "mexico", "moldova", "monaco", "mongolia", "montenegro",
    "morocco", "namibia", "nepal", "netherlands", "nederland", "niederlande",
    "new zealand", "nigeria", "north korea", "north macedonia", "norway", "norwegen",
    "oman", "pakistan", "palestine", "panama", "paraguay", "peru", "philippines",
    "poland", "polen", "portugal", "romania", "rumania", "rumanien", "romania",
    "russia", "russland", "saudi arabia", "serbia", "singapore", "slovakia",
    "slowakei", "slovenia", "slowenien", "south africa", "south korea", "spain",
    "spanien", "sweden", "schweden", "switzerland", "schweiz", "syria", "taiwan",
    "thailand", "tunisia", "turkey", "türkiye", "türkei", "ukraine", "united arab emirates",
    "united kingdom", "great britain", "england", "scotland", "wales", "usa",
    "united states", "united states of america", "uruguay", "uzbekistan", "venezuela",
    "vietnam", "zambia", "zimbabwe",
} - _GERMANY_LABELS


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).replace("ß", "ss")
    value = "".join(char for char in value if not unicodedata.combining(char)).casefold()
    value = re.sub(r"\b\d{5}\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


@lru_cache(maxsize=1)
def _municipalities() -> tuple[dict[str, dict], dict[str, set[str]]]:
    by_ags: dict[str, dict] = {}
    aliases: dict[str, set[str]] = {}
    resource = files("hiring_scraper").joinpath("elections", "btw25_municipalities.csv")
    with resource.open("r", encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            row["afd_second_votes"] = int(row["afd_second_votes"])
            row["valid_second_votes"] = int(row["valid_second_votes"])
            row["leading_party"] = row["leading_party"].strip()
            row["leading_party_vote_share_percent"] = float(row["leading_party_vote_share_percent"])
            row["postal_ballots_included"] = row["postal_ballots_included"].casefold() == "true"
            row["vote_share_percent"] = round(
                100 * row["afd_second_votes"] / row["valid_second_votes"], 2)
            by_ags[row["ags"]] = row
            name = row["name"]
            candidates = {name}
            if "," in name:
                candidates.add(name.split(",", 1)[0])
            for candidate in candidates:
                key = _normalize(candidate)
                if key:
                    aliases.setdefault(key, set()).add(row["ags"])
    return by_ags, aliases


def _state_hint(label: str) -> str | None:
    normalized = _normalize(label)
    for alias, state in _STATE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", normalized):
            return state
    return None


def _has_foreign_country_segment(label: str) -> bool:
    for part in re.split(r"[,;|\n]", label):
        normalized = _normalize(part)
        if normalized in _FOREIGN_COUNTRY_NAMES or normalized in _FOREIGN_COUNTRY_CODES:
            return True
    return False


@lru_cache(maxsize=4096)
def municipality_vote_result(label: str | None, country_code: str | None = None) -> dict | None:
    """Resolve a city label only when it uniquely identifies a municipality."""
    if not label or country_code and country_code.casefold() not in {"de", "deu"}:
        return None
    if _has_foreign_country_segment(label):
        return None
    by_ags, aliases = _municipalities()
    normalized_label = _normalize(label)
    if not normalized_label:
        return None
    state = _state_hint(label)
    candidates: set[str] = set()
    # Location labels often contain a street or postal code before the city, or
    # country/state names after it. Check comma-delimited locality components.
    for part in re.split(r"[,;|\n]", label):
        normalized = _normalize(part)
        if not normalized or re.match(r"^(?:regierungsbezirk|landkreis|kreis|bezirk|county)\b", normalized):
            continue
        candidates.update(aliases.get(normalized, set()))
        if normalized in _ENGLISH_CITY_ALIASES:
            candidates.update(aliases.get(_normalize(_ENGLISH_CITY_ALIASES[normalized]), set()))
        words = normalized.split()
        for start in range(len(words)):
            for end in range(start + 1, len(words) + 1):
                candidates.update(aliases.get(" ".join(words[start:end]), set()))
    # Also accept a complete normalized label for terse names such as Berlin.
    candidates.update(aliases.get(normalized_label, set()))
    if state:
        candidates = {ags for ags in candidates if by_ags[ags]["state"] == state}
    if len(candidates) != 1:
        return None
    row = by_ags[next(iter(candidates))]
    return {
        "municipality_code": row["ags"],
        "municipality": row["name"],
        "state": row["state"],
        "party": "AfD",
        "leading_party": row["leading_party"],
        "leading_party_vote_share_percent": row["leading_party_vote_share_percent"],
        "vote_share_percent": row["vote_share_percent"],
        "party_votes": row["afd_second_votes"],
        "valid_second_votes": row["valid_second_votes"],
        "postal_ballots_included": row["postal_ballots_included"],
        "election": ELECTION_NAME,
        "vote_type": "second votes",
        "source": ELECTION_SOURCE,
        "source_url": ELECTION_SOURCE_URL,
    }


def job_city_vote_results(job: dict) -> list[dict]:
    """Return election context for every unambiguously matched job location."""
    locations = job.get("locations")
    if not isinstance(locations, list):
        locations = []
    results = []
    has_non_german_location = False
    for location in locations:
        if isinstance(location, str):
            label, country = location, None
        elif isinstance(location, dict):
            label, country = location.get("label"), location.get("country_code")
            if country and str(country).casefold() not in {"de", "deu"}:
                has_non_german_location = True
        else:
            continue
        result = municipality_vote_result(label, country)
        if result and result["municipality_code"] not in {row["municipality_code"] for row in results}:
            results.append(result)
    if not results and not has_non_german_location:
        result = municipality_vote_result(job.get("location_text"))
        if result:
            results.append(result)
    return results
