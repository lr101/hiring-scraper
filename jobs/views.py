from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .collection import collect_source, collector_registry
from .forms import (
    BaseProfileLocationFormSet,
    ExclusionRuleForm,
    ProfileLocationFormSet,
    SearchProfileForm,
    WorkspaceUserForm,
)
from .matching import refresh_profile_matches, refresh_user_profile_matches
from .models import (
    CareerSource,
    Company,
    CrawlRun,
    ExclusionRule,
    GermanPlace,
    Job,
    JobMatch,
    SearchProfile,
    UserJobState,
    WorkspaceUser,
)
from .places import format_place_label, normalize_place_text
from .private import selected_workspace_user


def home(request: HttpRequest) -> HttpResponse:
    user = selected_workspace_user(request)
    matches = (
        _user_matches(user)
        .filter(is_new=True, job__closed_at__isnull=True)
        .exclude(job__user_states__user=user, job__user_states__status=UserJobState.Status.IGNORED)
        if user
        else JobMatch.objects.none()
    )
    return render(
        request, "jobs/home.html", {"matches": _unique_matches(matches)[:100], "active_user": user}
    )


def job_list(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    matches = _user_matches(user).filter(job__closed_at__isnull=True)
    if query := request.GET.get("q", "").strip():
        matches = matches.filter(job__title__icontains=query)
    if status := request.GET.get("status", "").strip():
        if status == UserJobState.Status.NONE:
            matches = matches.filter(
                Q(job__user_states__isnull=True)
                | Q(job__user_states__user=user, job__user_states__status=status)
            )
        else:
            matches = matches.filter(job__user_states__user=user, job__user_states__status=status)
    return render(
        request,
        "jobs/job_list.html",
        {
            "matches": _unique_matches(matches)[:100],
            "active_user": user,
            "status_choices": UserJobState.Status.choices,
        },
    )


def job_detail(request: HttpRequest, job_id: int) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    job = get_object_or_404(Job.objects.filter(matches__profile__user=user).distinct(), pk=job_id)
    state, _ = UserJobState.objects.get_or_create(user=user, job=job)
    if state.seen_at is None:
        state.seen_at = timezone.now()
        state.save(update_fields=["seen_at", "updated_at"])
    matches = job.matches.filter(profile__user=user).select_related("profile")
    return render(request, "jobs/job_detail.html", {"job": job, "state": state, "matches": matches})


@require_POST
def job_state(request: HttpRequest, job_id: int) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    job = get_object_or_404(Job.objects.filter(matches__profile__user=user).distinct(), pk=job_id)
    state, _ = UserJobState.objects.get_or_create(user=user, job=job)
    status = request.POST.get("status", UserJobState.Status.NONE)
    valid_statuses = {value for value, _ in UserJobState.Status.choices}
    state.status = status if status in valid_statuses else UserJobState.Status.NONE
    state.notes = request.POST.get("notes", "")
    state.save(update_fields=["status", "notes", "updated_at"])
    return redirect("jobs:job_detail", job_id=job.pk)


def company_list(request: HttpRequest) -> HttpResponse:
    companies = Company.objects.prefetch_related("sources").all()
    return render(request, "jobs/company_list.html", {"companies": companies})


def company_detail(request: HttpRequest, company_id: int) -> HttpResponse:
    company = get_object_or_404(Company.objects.prefetch_related("sources__runs"), pk=company_id)
    return render(request, "jobs/company_detail.html", {"company": company})


def source_list(request: HttpRequest) -> HttpResponse:
    sources = CareerSource.objects.select_related("company").all()
    return render(request, "jobs/source_list.html", {"sources": sources})


@require_POST
def source_toggle(request: HttpRequest, source_id: int) -> HttpResponse:
    source = get_object_or_404(CareerSource, pk=source_id)
    source.is_enabled = not source.is_enabled
    source.save(update_fields=["is_enabled"])
    return redirect("jobs:source_list")


@require_POST
def source_unblock(request: HttpRequest, source_id: int) -> HttpResponse:
    source = get_object_or_404(CareerSource, pk=source_id)
    source.blocked_at = None
    source.save(update_fields=["blocked_at"])
    return redirect("jobs:source_list")


@require_POST
def source_run(request: HttpRequest, source_id: int) -> HttpResponse:
    source = get_object_or_404(CareerSource, pk=source_id)
    if source.blocked_at is not None:
        messages.warning(request, "Unblock this source before running it.")
        return redirect("jobs:source_list")
    collect_source(source=source, registry=collector_registry)
    return redirect("jobs:run_list")


def run_list(request: HttpRequest) -> HttpResponse:
    runs = CrawlRun.objects.select_related("source__company").all()[:100]
    return render(request, "jobs/run_list.html", {"runs": runs})


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


def _user_matches(user: WorkspaceUser | None) -> QuerySet[JobMatch]:
    if user is None:
        return JobMatch.objects.none()
    return (
        JobMatch.objects.filter(profile__user=user)
        .select_related("job__source__company", "profile")
        .order_by("-score", "-first_matched_at")
        .distinct()
    )


def _unique_matches(matches: QuerySet[JobMatch]) -> list[JobMatch]:
    unique: dict[int, JobMatch] = {}
    for match in matches:
        unique.setdefault(match.job_id, match)
    return list(unique.values())


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
