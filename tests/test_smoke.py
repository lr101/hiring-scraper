import pytest


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
