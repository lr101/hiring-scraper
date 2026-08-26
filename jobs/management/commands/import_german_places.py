from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any, TextIO

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import CharField, FloatField, OuterRef, Subquery

from jobs.models import GermanPlace, ProfileLocation
from jobs.places import normalize_place_text


class Command(BaseCommand):
    help = "Import local GeoNames cities500 and optional Germany postal-code archives."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--cities", required=True, help="Path to a downloaded GeoNames cities500.zip"
        )
        parser.add_argument(
            "--postal-codes", help="Optional path to a downloaded GeoNames Germany postal-code ZIP"
        )
        parser.add_argument(
            "--snapshot",
            required=True,
            help="GeoNames source snapshot date, such as 2026-08-26",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        cities_path = _path_option(options, "cities")
        postal_codes_path = _optional_path_option(options, "postal_codes")
        snapshot = _string_option(options, "snapshot")
        city_count, city_place_ids = _import_cities(cities_path, snapshot)
        postal_count, postal_place_ids = (
            _import_postal_codes(postal_codes_path, snapshot) if postal_codes_path else (0, set())
        )
        _sync_profile_locations(city_place_ids | postal_place_ids)
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {city_count} city records and {postal_count} postal-code records."
            )
        )
        self.stdout.write(
            "Contains information from GeoNames.org, licensed under CC BY 4.0; "
            "filtered and normalized by this application."
        )


def _path_option(options: dict[str, object], key: str) -> Path:
    value = _string_option(options, key)
    path = Path(value)
    if not path.is_file():
        raise CommandError(f"{key.replace('_', '-')} archive does not exist: {path}")
    return path


def _optional_path_option(options: dict[str, object], key: str) -> Path | None:
    value = options.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CommandError(f"{key.replace('_', '-')} must be a path")
    path = Path(value)
    if not path.is_file():
        raise CommandError(f"{key.replace('_', '-')} archive does not exist: {path}")
    return path


def _string_option(options: dict[str, object], key: str) -> str:
    value = options.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandError(f"{key.replace('_', '-')} is required")
    return value.strip()


def _import_cities(archive_path: Path, snapshot: str) -> tuple[int, set[int]]:
    count = 0
    place_ids: set[int] = set()
    with _open_first_text_file(archive_path) as rows:
        for line in rows:
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 15 or columns[8] != "DE":
                continue
            source_id, name, latitude, longitude, admin_area, population = (
                columns[0],
                columns[1],
                columns[4],
                columns[5],
                columns[10],
                columns[14],
            )
            if not source_id or not name or not latitude or not longitude:
                continue
            place, _ = GermanPlace.objects.update_or_create(
                source_id=f"geonames:{source_id}",
                defaults={
                    "name": name,
                    "normalized_name": normalize_place_text(name),
                    "postal_code": "",
                    "admin_area": admin_area,
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                    "population": int(population) if population.isdigit() else None,
                    "source_kind": GermanPlace.SourceKind.CITY,
                    "source_snapshot": snapshot,
                },
            )
            place_ids.add(place.pk)
            count += 1
    return count, place_ids


def _import_postal_codes(archive_path: Path, snapshot: str) -> tuple[int, set[int]]:
    count = 0
    place_ids: set[int] = set()
    with _open_first_text_file(archive_path) as rows:
        for line in rows:
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 12 or columns[0] != "DE":
                continue
            postal_code, name, admin_area, latitude, longitude = (
                columns[1],
                columns[2],
                columns[3],
                columns[9],
                columns[10],
            )
            if not postal_code or not name or not latitude or not longitude:
                continue
            admin_codes = [columns[4], columns[6], columns[8]]
            source_id = (
                "geonames-postal:"
                + hashlib.sha256("\x1f".join([postal_code, *admin_codes]).encode()).hexdigest()
            )
            place, _ = GermanPlace.objects.update_or_create(
                source_id=source_id,
                defaults={
                    "name": name,
                    "normalized_name": normalize_place_text(name),
                    "postal_code": postal_code,
                    "admin_area": admin_area,
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                    "population": None,
                    "source_kind": GermanPlace.SourceKind.POSTAL_CODE,
                    "source_snapshot": snapshot,
                },
            )
            place_ids.add(place.pk)
            count += 1
    return count, place_ids


def _sync_profile_locations(place_ids: set[int]) -> None:
    if not place_ids:
        return
    places = GermanPlace.objects.filter(pk=OuterRef("place_id"))
    ProfileLocation.objects.filter(place_id__in=place_ids).update(
        city=Subquery(places.values("name")[:1], output_field=CharField()),
        latitude=Subquery(places.values("latitude")[:1], output_field=FloatField()),
        longitude=Subquery(places.values("longitude")[:1], output_field=FloatField()),
    )


def _open_first_text_file(archive_path: Path) -> TextIO:
    archive = zipfile.ZipFile(archive_path)
    member = next((name for name in archive.namelist() if name.endswith(".txt")), None)
    if member is None:
        archive.close()
        raise CommandError(f"No .txt data file found in {archive_path}")
    return io.TextIOWrapper(archive.open(member, "r"), encoding="utf-8")
