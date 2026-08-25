import pytest
from django.urls import reverse

from jobs.models import WorkspaceUser


@pytest.mark.django_db
def test_user_can_be_selected_from_every_page(client) -> None:  # type: ignore[no-untyped-def]
    ada = WorkspaceUser.objects.create(name="Ada")
    WorkspaceUser.objects.create(name="Grace")

    switch_response = client.post(reverse("jobs:select_user"), {"user": ada.pk}, follow=True)

    assert switch_response.status_code == 200
    assert client.cookies["workspace_user"].value == str(ada.pk)
    assert switch_response.context["active_user"] == ada
    assert b"Ada" in switch_response.content
    assert b"Grace" in switch_response.content


@pytest.mark.django_db
def test_account_can_be_created_without_authentication(client) -> None:  # type: ignore[no-untyped-def]
    response = client.post(reverse("jobs:account_list"), {"name": "Linus"}, follow=True)

    assert response.status_code == 200
    assert WorkspaceUser.objects.filter(name="Linus").exists()
    assert b"Linus" in response.content
