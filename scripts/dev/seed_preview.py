"""Add a small, clearly synthetic starting point to an isolated app preview."""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from hiring_scraper.app.database import IS_SQLITE, SessionLocal, engine, initialize_sqlite_schema
from hiring_scraper.app.models import ConfiguredLocation, UserProfile, utcnow


KARLSRUHE = {
    "label": "Karlsruhe, Baden-Württemberg, Deutschland",
    "city": "Karlsruhe",
    "state": "Baden-Württemberg",
    "latitude": 49.0068705,
    "longitude": 8.4034195,
    "radius_km": 35,
}
DEMO_PROFILE_NAME = "Preview demo profile"


def main() -> None:
    if IS_SQLITE:
        initialize_sqlite_schema(engine)

    created_location = False
    created_profile = False
    now = utcnow()
    with SessionLocal.begin() as session:
        location = session.scalar(select(ConfiguredLocation).where(
            ConfiguredLocation.city == KARLSRUHE["city"],
            ConfiguredLocation.search_scope == "radius",
        ).order_by(ConfiguredLocation.id).limit(1))
        if location is None:
            session.add(ConfiguredLocation(
                **KARLSRUHE,
                search_scope="radius",
                postcode=None,
                interval_days=7,
                enabled=True,
                next_run_at=now + timedelta(days=7),
                created_at=now,
                updated_at=now,
            ))
            created_location = True

        profile = session.scalar(select(UserProfile).where(UserProfile.name == DEMO_PROFILE_NAME).limit(1))
        if profile is None:
            preferences = {
                "summary": "Synthetic preview profile for exploring local job matches and application tracking.",
                "skills": ["Python", "SQL", "Software testing", "Data analysis", "Project management"],
                "desired_roles": ["Software engineer", "Quality assurance engineer", "Data analyst"],
                "secondary_roles": [],
                "work_styles": [],
                "employment_types": [],
                "seniority_levels": [],
                "experience_years": None,
                "languages": [],
                "language_levels": {},
                "excluded_terms": [],
                "skill_evidence": {},
                "search_area": {
                    "label": KARLSRUHE["label"],
                    "city": KARLSRUHE["city"],
                    "latitude": KARLSRUHE["latitude"],
                    "longitude": KARLSRUHE["longitude"],
                    "radius_km": KARLSRUHE["radius_km"],
                    "country_code": "de",
                },
                "education": [],
                "certifications": [],
                "matching_defaults": {"min_match_score": 0, "include_unknown": True},
            }
            session.add(UserProfile(name=DEMO_PROFILE_NAME, preferences=preferences,
                                    created_at=now, updated_at=now))
            created_profile = True

    print(json.dumps({
        "karlsruhe_location": "created" if created_location else "already present",
        "demo_profile": "created" if created_profile else "already present",
    }))


if __name__ == "__main__":
    main()
