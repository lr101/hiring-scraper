from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest

from .models import WorkspaceUser


def selected_workspace_user(request: HttpRequest) -> WorkspaceUser | None:
    """Return only the account explicitly selected in this request's cookie."""
    selected = request.COOKIES.get(settings.WORKSPACE_USER_COOKIE)
    try:
        user_id = int(selected) if selected is not None else 0
    except ValueError:
        return None
    if user_id <= 0:
        return None
    return WorkspaceUser.objects.filter(pk=user_id, is_active=True).first()
