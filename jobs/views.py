from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    BaseProfileLocationFormSet,
    ExclusionRuleForm,
    ProfileLocationFormSet,
    SearchProfileForm,
    WorkspaceUserForm,
)
from .matching import refresh_profile_matches, refresh_user_profile_matches
from .models import ExclusionRule, GermanPlace, SearchProfile, WorkspaceUser
from .places import format_place_label, normalize_place_text
from .private import selected_workspace_user


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "jobs/home.html")


@require_POST
def select_user(request: HttpRequest) -> HttpResponse:
    selected_user = request.POST.get("user")
    try:
        user_id = int(selected_user) if selected_user is not None else 0
    except ValueError:
        raise Http404("Unknown account") from None
    if user_id <= 0:
        raise Http404("Unknown account")

    user = get_object_or_404(WorkspaceUser, pk=user_id, is_active=True)
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


def profile_list(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    profiles = SearchProfile.objects.filter(user=user).prefetch_related("profile_locations__place")
    return render(request, "jobs/profile_list.html", {"profiles": profiles})


def profile_create(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    profile = SearchProfile(user=user)
    return _profile_form_response(request=request, profile=profile, user=user, creating=True)


def profile_edit(request: HttpRequest, profile_id: int) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    profile = get_object_or_404(SearchProfile, pk=profile_id, user=user)
    return _profile_form_response(request=request, profile=profile, user=user, creating=False)


def profile_delete(request: HttpRequest, profile_id: int) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    profile = get_object_or_404(SearchProfile, pk=profile_id, user=user)
    if request.method == "POST":
        profile.delete()
        messages.success(request, "Deleted search profile.")
        return redirect("jobs:profile_list")
    return render(request, "jobs/profile_confirm_delete.html", {"profile": profile})


def exclusion_list(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    return render(
        request,
        "jobs/exclusion_list.html",
        {"rules": ExclusionRule.objects.filter(user=user)},
    )


def exclusion_create(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    form = ExclusionRuleForm(request.POST or None, instance=ExclusionRule(user=user))
    if request.method == "POST" and form.is_valid():
        form.save()
        refresh_user_profile_matches(user=user)
        messages.success(request, "Added exclusion rule.")
        return redirect("jobs:exclusion_list")
    return render(
        request, "jobs/exclusion_form.html", {"form": form, "title": "Add exclusion rule"}
    )


@require_POST
def exclusion_toggle(request: HttpRequest, rule_id: int) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    rule = get_object_or_404(ExclusionRule, pk=rule_id, user=user)
    rule.is_enabled = not rule.is_enabled
    rule.save(update_fields=["is_enabled"])
    refresh_user_profile_matches(user=user)
    return redirect("jobs:exclusion_list")


def exclusion_delete(request: HttpRequest, rule_id: int) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    rule = get_object_or_404(ExclusionRule, pk=rule_id, user=user)
    if request.method == "POST":
        rule.delete()
        refresh_user_profile_matches(user=user)
        messages.success(request, "Deleted exclusion rule.")
        return redirect("jobs:exclusion_list")
    return render(request, "jobs/exclusion_confirm_delete.html", {"rule": rule})


def place_search(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    query = request.GET.get("q", "")
    normalized = _normalize_place_query(query)
    places = _matching_places(query, normalized)
    return render(request, "jobs/place_search.html", {"query": query, "places": places})


def place_search_json(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    query = request.GET.get("q", "")
    places = _matching_places(query, _normalize_place_query(query))
    return JsonResponse(
        {"results": [{"id": place.pk, "label": format_place_label(place)} for place in places]}
    )


def _profile_form_response(
    *, request: HttpRequest, profile: SearchProfile, user: WorkspaceUser, creating: bool
) -> HttpResponse:
    form = SearchProfileForm(request.POST or None, instance=profile)
    form.instance.user = user
    formset = ProfileLocationFormSet(
        request.POST or None,
        instance=profile,
        prefix="profile_locations",
        form_kwargs={"allowed_place_ids": _allowed_place_ids(request, profile)},
    )
    assert isinstance(formset, BaseProfileLocationFormSet)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        if not any(formset.selected_places()) and not form.cleaned_data["include_remote"]:
            form.add_error("include_remote", "Add a German city radius or include remote jobs.")
        else:
            with transaction.atomic():
                saved_profile = form.save()
                formset.instance = saved_profile
                formset.save()
            refresh_profile_matches(profile=saved_profile)
            messages.success(
                request, "Created search profile." if creating else "Updated search profile."
            )
            return redirect("jobs:profile_list")
    return render(
        request,
        "jobs/profile_form.html",
        {"form": form, "formset": formset, "creating": creating, "profile": profile},
    )


def _private_user_or_redirect(request: HttpRequest) -> WorkspaceUser | HttpResponse:
    user = selected_workspace_user(request)
    if user is None:
        messages.info(request, "Select an account to manage profiles and exclusions.")
        return redirect("jobs:account_list")
    return user


def _normalize_place_query(value: str) -> str:
    return normalize_place_text(value)


def _matching_places(query: str, normalized: str):  # type: ignore[no-untyped-def]
    if not normalized:
        return GermanPlace.objects.none()
    places = GermanPlace.objects.filter(
        normalized_name__contains=normalized
    ) | GermanPlace.objects.filter(postal_code__startswith=query.strip())
    return places.order_by("name", "admin_area", "postal_code")[:50]


def _allowed_place_ids(request: HttpRequest, profile: SearchProfile) -> set[int]:
    if request.method != "POST":
        if profile.pk is None:
            return set()
        return set(profile.profile_locations.values_list("place_id", flat=True))
    place_ids: set[int] = set()
    for key, value in request.POST.items():
        if (
            key.startswith("profile_locations-")
            and key.endswith("-place")
            and isinstance(value, str)
        ):
            try:
                place_ids.add(int(value))
            except ValueError:
                continue
    return place_ids
