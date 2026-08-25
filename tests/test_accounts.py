import pytest
from django.urls import reverse

from jobs.models import WorkspaceUser


@pytest.mark.django_db
def test_home_renders_account_selector(client) -> None:  # type: ignore[no-untyped-def]
    ada = WorkspaceUser.objects.create(name="Ada")
    WorkspaceUser.objects.create(name="Grace")

    response = client.get(reverse("jobs:home"))

    assert response.status_code == 200
    assert b'<select id="workspace-user" name="user"' in response.content
    assert b'<option value="' + str(ada.pk).encode() in response.content
    assert b"Ada" in response.content
    assert b"Grace" in response.content


@pytest.mark.django_db
def test_accounts_renders_account_selector(client) -> None:  # type: ignore[no-untyped-def]
    ada = WorkspaceUser.objects.create(name="Ada")
    WorkspaceUser.objects.create(name="Grace")

    response = client.get(reverse("jobs:account_list"))

    assert response.status_code == 200
    assert b'<select id="workspace-user" name="user"' in response.content
    assert b'<option value="' + str(ada.pk).encode() in response.content
    assert b"Ada" in response.content
    assert b"Grace" in response.content


@pytest.mark.django_db
def test_empty_account_selector_cannot_be_submitted(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get(reverse("jobs:home"))

    assert response.status_code == 200
    assert b"<option disabled>No accounts</option>" in response.content


@pytest.mark.django_db
def test_user_selection_sets_active_account(client) -> None:  # type: ignore[no-untyped-def]
    ada = WorkspaceUser.objects.create(name="Ada")
    WorkspaceUser.objects.create(name="Grace")

    switch_response = client.post(reverse("jobs:select_user"), {"user": ada.pk}, follow=True)

    assert switch_response.status_code == 200
    assert client.cookies["workspace_user"].value == str(ada.pk)
    assert switch_response.context["active_user"] == ada
    assert b"Ada" in switch_response.content
    assert b"Grace" in switch_response.content


@pytest.mark.django_db
@pytest.mark.parametrize("selector", [None, "", "not-a-primary-key"])
def test_user_selection_rejects_missing_or_malformed_selector(client, selector) -> None:  # type: ignore[no-untyped-def]
    data = {} if selector is None else {"user": selector}

    response = client.post(reverse("jobs:select_user"), data)

    assert response.status_code in {400, 404}
    assert "workspace_user" not in client.cookies


@pytest.mark.django_db
def test_user_selection_rejects_inactive_account(client) -> None:  # type: ignore[no-untyped-def]
    inactive_user = WorkspaceUser.objects.create(name="Inactive", is_active=False)

    response = client.post(reverse("jobs:select_user"), {"user": inactive_user.pk})

    assert response.status_code == 404
    assert "workspace_user" not in client.cookies


@pytest.mark.django_db
def test_account_can_be_created_without_authentication(client) -> None:  # type: ignore[no-untyped-def]
    response = client.post(reverse("jobs:account_list"), {"name": "Linus"}, follow=True)

    assert response.status_code == 200
    assert WorkspaceUser.objects.filter(name="Linus").exists()
    assert b"Linus" in response.content
