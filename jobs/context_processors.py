from typing import Any

from django.conf import settings
from django.http import HttpRequest

from .models import WorkspaceUser


def workspace_users(request: HttpRequest) -> dict[str, Any]:
    users = list(WorkspaceUser.objects.filter(is_active=True).order_by("name"))
    selected = request.COOKIES.get(settings.WORKSPACE_USER_COOKIE)
    active_user = next((user for user in users if str(user.pk) == selected), None)
    if active_user is None and users:
        active_user = users[0]
    return {"workspace_users": users, "active_user": active_user}
