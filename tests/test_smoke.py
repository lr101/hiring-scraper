import os
import subprocess
import sys

import pytest
from django.contrib.staticfiles import finders


@pytest.mark.django_db
def test_home_page_is_available(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/")

    assert response.status_code == 200
    assert b"New jobs" in response.content


@pytest.mark.django_db
def test_health_endpoint_reports_database_is_ready(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_stylesheet_is_discoverable() -> None:
    assert finders.find("app.css") is not None


def test_csrf_trusted_origins_are_loaded_from_environment() -> None:
    environment = os.environ.copy()
    environment["DJANGO_CSRF_TRUSTED_ORIGINS"] = (
        "https://jobs.example.test, https://admin.example.test"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; print('|'.join(settings.CSRF_TRUSTED_ORIGINS))",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.stdout.strip() == "https://jobs.example.test|https://admin.example.test"
