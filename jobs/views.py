from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import WorkspaceUserForm
from .models import WorkspaceUser


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "jobs/home.html")


@require_POST
def select_user(request: HttpRequest) -> HttpResponse:
    user = get_object_or_404(WorkspaceUser, pk=request.POST.get("user"), is_active=True)
    user.last_selected_at = timezone.now()
    user.save(update_fields=["last_selected_at"])
    response = redirect("jobs:home")
    response.set_cookie(
        settings.WORKSPACE_USER_COOKIE,
        str(user.pk),
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="Lax",
    )
    return response


def account_list(request: HttpRequest) -> HttpResponse:
    form = WorkspaceUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Created account {user.name}.")
        return redirect("jobs:account_list")
    return render(
        request,
        "jobs/account_list.html",
        {"form": form, "accounts": WorkspaceUser.objects.order_by("name")},
    )
