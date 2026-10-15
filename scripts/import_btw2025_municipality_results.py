#!/usr/bin/env python3
"""Aggregate the Federal Returning Officer's 2025 district CSV by municipality.

Download btw25_wbz.zip from the official source, then run this script with the
archive path. It writes municipality-level AfD second-vote totals and the party
with the most second votes, including that party's share of valid votes.
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path
import re
import unicodedata
import zipfile


STATES = {
    "01": "Schleswig-Holstein", "02": "Hamburg", "03": "Niedersachsen",
    "04": "Bremen", "05": "Nordrhein-Westfalen", "06": "Hessen",
    "07": "Rheinland-Pfalz", "08": "Baden-Württemberg", "09": "Bayern",
    "10": "Saarland", "11": "Berlin", "12": "Brandenburg",
    "13": "Mecklenburg-Vorpommern", "14": "Sachsen", "15": "Sachsen-Anhalt",
    "16": "Thüringen",
}


def integer(value: str) -> int:
    value = value.strip().replace(".", "").replace(",", "")
    return int(value) if value.isdigit() else 0


def name_key(value: str) -> str:
    value = re.sub(r"\s*\(Teil\)", "", value, flags=re.IGNORECASE)
    value = unicodedata.normalize("NFKD", value).replace("ß", "ss")
    value = "".join(char for char in value if not unicodedata.combining(char)).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def municipality_name(value: str) -> str:
    return re.sub(r"\s*\(Teil\)$", "", value, flags=re.IGNORECASE).strip()


def aggregate(archive: Path) -> list[dict[str, str | int | float]]:
    with zipfile.ZipFile(archive) as bundle:
        csv_name = next(name for name in bundle.namelist()
                        if name.endswith("btw25_wbz_ergebnisse.csv"))
        with bundle.open(csv_name) as raw:
            rows = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter=";")
            header = next(row for row in rows if row and row[0] == "Wahlkreis")
            valid_votes_index = header.index("Gültige - Zweitstimmen")
            party_vote_indexes = {
                column.removesuffix(" - Zweitstimmen"): index
                for index, column in enumerate(header)
                if column.endswith(" - Zweitstimmen")
                and column not in {"Ungültige - Zweitstimmen", "Gültige - Zweitstimmen"}
            }
            required_vote_index = max(valid_votes_index, *party_vote_indexes.values())
            totals: dict[str, dict] = {}
            exact_postal_rows = []
            shared_postal_groups = []
            for row in rows:
                if len(row) <= required_vote_index or len(row[1]) != 2:
                    continue
                state, district, county, association = row[1:5]
                region = (state, district, county, association)
                name = row[8].strip()
                party_votes = {party: integer(row[index]) for party, index in party_vote_indexes.items()}
                afd_votes = party_votes.get("AfD", 0)
                valid_votes = integer(row[valid_votes_index])
                # Berlin and Hamburg are split into borough codes in this release;
                # their city-level result is the sum across all boroughs.
                if row[1] == "02":
                    ags, city_name = "02000000", "Hamburg, Freie und Hansestadt"
                elif row[1] == "11":
                    ags, city_name = "11000000", "Berlin, Stadt"
                else:
                    if name.startswith("Briefwahl EG "):
                        exact_postal_rows.append((region, name.removeprefix("Briefwahl EG ").strip(),
                                                  party_votes, valid_votes))
                        continue
                    if name.startswith("Briefwahl "):
                        # These postal ballots were counted for a shared local
                        # authority, so they cannot be assigned to one municipality.
                        shared_postal_groups.append(region)
                        continue
                    # AGS is Land (2), Regierungsbezirk (1), Kreis (2), Gemeinde (3).
                    ags, city_name = state + district + county + row[5], municipality_name(name)
                city = totals.setdefault(ags, {
                    "ags": ags, "name": city_name,
                    "state": STATES.get(state, ""),
                    "afd_second_votes": 0, "valid_second_votes": 0,
                    "leading_party": "", "postal_ballots_included": True, "_region": region,
                    "_second_vote_party_counts": {party: 0 for party in party_vote_indexes},
                })
                city["afd_second_votes"] += afd_votes
                city["valid_second_votes"] += valid_votes
                for party, votes in party_votes.items():
                    city["_second_vote_party_counts"][party] += votes

    by_region_and_name: dict[tuple, list[dict]] = {}
    for city in totals.values():
        by_region_and_name.setdefault((city["_region"], name_key(city["name"])), []).append(city)
    for region, target_name, party_votes, valid_votes in exact_postal_rows:
        candidates = by_region_and_name.get((region, name_key(target_name)), [])
        if len(candidates) == 1:
            candidates[0]["afd_second_votes"] += party_votes.get("AfD", 0)
            candidates[0]["valid_second_votes"] += valid_votes
            for party, votes in party_votes.items():
                candidates[0]["_second_vote_party_counts"][party] += votes
        else:
            shared_postal_groups.append(region)

    for state, district, county, association in shared_postal_groups:
        if association != "0000":
            affected = (state, district, county, association)
        else:
            # If a shared postal unit has no association code, suppress all
            # municipality scores in its county rather than imply a complete total.
            affected = (state, district, county)
        for city in totals.values():
            if city["_region"][:len(affected)] == affected:
                city["postal_ballots_included"] = False

    for city in totals.values():
        party_counts = city["_second_vote_party_counts"]
        most_votes = max(party_counts.values(), default=0)
        city["leading_party"] = " / ".join(sorted(
            party for party, votes in party_counts.items() if votes == most_votes and votes > 0))
        city["leading_party_vote_share_percent"] = round(
            100 * most_votes / city["valid_second_votes"], 2) if city["valid_second_votes"] else 0.0

    return sorted((row for row in totals.values() if row["valid_second_votes"] > 0),
                  key=lambda row: row["ags"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="path to the official btw25_wbz.zip")
    parser.add_argument("--output", type=Path, default=Path("hiring_scraper/elections/btw25_municipalities.csv"))
    args = parser.parse_args()
    rows = aggregate(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=(
            "ags", "name", "state", "leading_party", "leading_party_vote_share_percent",
            "afd_second_votes", "valid_second_votes", "postal_ballots_included"))
        writer.writeheader()
        writer.writerows({key: value for key, value in row.items() if not key.startswith("_")}
                          for row in rows)
    print(f"Wrote {len(rows)} municipalities to {args.output}")


if __name__ == "__main__":
    main()
