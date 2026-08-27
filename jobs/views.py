from django.conf import settings
from django.contrib import messages
from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .collection import collect_source, collector_registry
from .company_discovery import CompanyDiscoveryError, DiscoveredCompany, discover_company
from .company_locations import (
    CompanyLocationLookupError,
    discover_companies_in_place,
)
from .forms import (
    CityMonitoringTargetForm,
    CompanyMonitoringTargetForm,
    ExclusionRuleForm,
    SearchProfileForm,
    WorkspaceUserForm,
)
from .locations import LocationLookupError, location_result, search_locations
from .matching import refresh_profile_matches, refresh_user_profile_matches
from .models import (
    CareerSource,
    Company,
    CrawlRun,
    ExclusionRule,
    GermanPlace,
    Job,
    JobMatch,
    MonitoringTarget,
    SearchProfile,
    UserJobState,
    WorkspaceUser,
)
from .monitoring import filter_jobs_for_user
from .private import selected_workspace_user
from .source_setup import MonitoredCompany, monitor_company


def home(request: HttpRequest) -> HttpResponse:
    return feed(request)


def feed(request: HttpRequest) -> HttpResponse:
    user = selected_workspace_user(request)
    has_profiles = bool(user and SearchProfile.objects.filter(user=user).exists())
    has_targets = bool(user and MonitoringTarget.objects.filter(user=user).exists())
    has_company_targets = bool(
        user
        and MonitoringTarget.objects.filter(user=user, kind=MonitoringTarget.Kind.COMPANY).exists()
    )
    ignored_job_ids = (
        UserJobState.objects.filter(user=user, status=UserJobState.Status.IGNORED).values("job_id")
        if user
        else UserJobState.objects.none().values("job_id")
    )
    matches = (
        _user_matches(user)
        .filter(is_new=True, job__closed_at__isnull=True)
        .exclude(job_id__in=ignored_job_ids)
        if user
        else JobMatch.objects.none()
    )
    visible_matches = _unique_matches(matches)
    if user:
        visible_job_ids = {
            job.pk
            for job in filter_jobs_for_user(
                user=user, jobs=[match.job for match in visible_matches]
            )
        }
        visible_matches = [match for match in visible_matches if match.job_id in visible_job_ids]
    return render(
        request,
        "jobs/home.html",
        {
            "matches": visible_matches[:100],
            "active_user": user,
            "has_profiles": has_profiles,
            "has_targets": has_targets,
            "has_company_targets": has_company_targets,
        },
    )


def health(request: HttpRequest) -> JsonResponse:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


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
    job = get_object_or_404(
        Job.objects.filter(Q(matches__profile__user=user) | Q(user_states__user=user)).distinct(),
        pk=job_id,
    )
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
    job = get_object_or_404(
        Job.objects.filter(Q(matches__profile__user=user) | Q(user_states__user=user)).distinct(),
        pk=job_id,
    )
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


def setup(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    return _setup_response(request=request, user=user)


def _setup_response(
    *,
    request: HttpRequest,
    user: WorkspaceUser,
    company_form: CompanyMonitoringTargetForm | None = None,
    city_form: CityMonitoringTargetForm | None = None,
) -> HttpResponse:
    return render(
        request,
        "jobs/setup.html",
        {
            "company_form": company_form or CompanyMonitoringTargetForm(user=user),
            "city_form": city_form or CityMonitoringTargetForm(),
            "profiles": SearchProfile.objects.filter(user=user),
            "company_targets": MonitoringTarget.objects.filter(
                user=user, kind=MonitoringTarget.Kind.COMPANY
            ).select_related("company"),
            "city_targets": MonitoringTarget.objects.filter(
                user=user, kind=MonitoringTarget.Kind.CITY
            ).select_related("place"),
        },
    )


@require_POST
def company_target_create(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    form = CompanyMonitoringTargetForm(request.POST, user=user)
    if form.is_valid():
        domain = form.cleaned_data["domain"]
        company = Company.objects.filter(domain=domain).order_by("pk").first()
        discovery = None
        if company is None:
            try:
                discovery = discover_company(domain)
            except CompanyDiscoveryError as error:
                form.add_error("domain", str(error))
        if (company is not None or discovery is not None) and not form.errors:
            monitored_company = _monitor_company(user=user, company=company, discovery=discovery)
            run = _run_initial_scan(monitored_company.source)
            assert monitored_company.company is not None
            messages.success(
                request,
                _company_added_message(monitored_company.company, run),
            )
            return redirect("jobs:setup")
    if form.errors:
        return _setup_response(request=request, user=user, company_form=form)
    return _setup_response(request=request, user=user, company_form=form)


@require_POST
def city_target_create(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    form = CityMonitoringTargetForm(request.POST)
    if form.is_valid():
        place = form.cleaned_data["place"]
        radius_km = form.cleaned_data["radius_km"]
        target, created = MonitoringTarget.objects.get_or_create(
            user=user,
            kind=MonitoringTarget.Kind.CITY,
            place=place,
            defaults={"radius_km": radius_km},
        )
        if not created and target.radius_km != form.cleaned_data["radius_km"]:
            target.radius_km = radius_km
            target.save(update_fields=["radius_km"])
        try:
            added_companies, scans, unreadable_websites = _discover_and_scan_city(
                user=user, place=place, radius_km=radius_km
            )
        except CompanyLocationLookupError as error:
            messages.warning(request, f"{place.name} was saved, but company search failed: {error}")
            return redirect("jobs:setup")
        messages.success(
            request,
            _city_added_message(
                place_name=place.name,
                added_companies=added_companies,
                scans=scans,
                unreadable_websites=unreadable_websites,
            ),
        )
        return redirect("jobs:setup")
    return _setup_response(request=request, user=user, city_form=form)


@require_POST
def city_target_refresh(request: HttpRequest, target_id: int) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    target = get_object_or_404(
        MonitoringTarget.objects.select_related("place"),
        pk=target_id,
        user=user,
        kind=MonitoringTarget.Kind.CITY,
    )
    assert target.place is not None
    try:
        added_companies, scans, unreadable_websites = _discover_and_scan_city(
            user=user, place=target.place, radius_km=target.radius_km
        )
    except CompanyLocationLookupError as error:
        messages.warning(request, f"{target.place.name} search failed: {error}")
        return redirect("jobs:setup")
    messages.success(
        request,
        _city_added_message(
            place_name=target.place.name,
            added_companies=added_companies,
            scans=scans,
            unreadable_websites=unreadable_websites,
        ),
    )
    return redirect("jobs:setup")


@require_POST
def monitoring_target_delete(request: HttpRequest, target_id: int) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    target = get_object_or_404(MonitoringTarget, pk=target_id, user=user)
    target.delete()
    return redirect("jobs:setup")


def _monitor_company(
    *,
    user: WorkspaceUser,
    company: Company | None = None,
    discovery: DiscoveredCompany | None = None,
) -> MonitoredCompany:
    return monitor_company(user=user, company=company, discovery=discovery)


def _run_initial_scan(source: CareerSource) -> CrawlRun | None:
    if not source.is_enabled or source.blocked_at is not None:
        return None
    return collect_source(source=source, registry=collector_registry)


def _discover_and_scan_city(
    *, user: WorkspaceUser, place: GermanPlace, radius_km: int
) -> tuple[int, list[CrawlRun], int]:
    discovery = discover_companies_in_place(place, radius_km=radius_km)
    added_companies = 0
    scans: list[CrawlRun] = []
    for company_discovery in discovery.companies:
        monitored_company = _monitor_company(user=user, discovery=company_discovery)
        if monitored_company.target_created:
            added_companies += 1
        if monitored_company.target_created or monitored_company.source_created:
            run = _run_initial_scan(monitored_company.source)
            if run is not None:
                scans.append(run)
    return added_companies, scans, discovery.unreadable_websites


def _company_added_message(company: Company, run: CrawlRun | None) -> str:
    if run is None:
        return f"Added {company.name}. Its source is disabled or blocked, so no scan was started."
    if run.status == CrawlRun.Status.SUCCESS:
        return f"Added {company.name}. Initial scan found {run.jobs_created} new jobs."
    return f"Added {company.name}. Initial scan did not finish: {run.get_status_display()}."


def _city_added_message(
    *,
    place_name: str,
    added_companies: int,
    scans: list[CrawlRun],
    unreadable_websites: int,
) -> str:
    if added_companies == 0 and not scans:
        message = f"Saved {place_name}. No new companies with readable public websites were found."
    else:
        new_jobs = sum(run.jobs_created for run in scans if run.status == CrawlRun.Status.SUCCESS)
        if added_companies:
            message = f"Saved {place_name}. Added {added_companies} companies and "
        else:
            message = f"Saved {place_name}. Rechecked company sources and "
        message += f"found {new_jobs} new jobs."
    if unreadable_websites:
        message += f" {unreadable_websites} website could not be read."
    return message


def profile(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    workflow_jobs = UserJobState.objects.filter(user=user).exclude(status=UserJobState.Status.NONE)
    status = request.GET.get("status", "")
    valid_statuses = {value for value, _label in UserJobState.Status.choices}
    if status in valid_statuses and status != UserJobState.Status.NONE:
        workflow_jobs = workflow_jobs.filter(status=status)
    sort = request.GET.get("sort", "recent")
    if sort == "company":
        workflow_jobs = workflow_jobs.order_by("job__source__company__name", "job__title", "pk")
    else:
        sort = "recent"
        workflow_jobs = workflow_jobs.order_by("-updated_at", "-pk")
    return render(
        request,
        "jobs/profile_page.html",
        {
            "profiles": SearchProfile.objects.filter(user=user),
            "workflow_jobs": workflow_jobs.select_related("job__source__company")[:100],
            "status_choices": [
                choice
                for choice in UserJobState.Status.choices
                if choice[0] != UserJobState.Status.NONE
            ],
            "selected_status": status,
            "selected_sort": sort,
        },
    )


def profile_list(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    profiles = SearchProfile.objects.filter(user=user)
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


@require_GET
def location_search(request: HttpRequest) -> HttpResponse:
    user = _private_user_or_redirect(request)
    if isinstance(user, HttpResponse):
        return user
    query = request.GET.get("q", "").strip()
    try:
        places = search_locations(query)
    except LocationLookupError as error:
        return JsonResponse({"results": [], "error": str(error)}, status=502)
    return JsonResponse({"results": [location_result(place) for place in places]})


def _profile_form_response(
    *, request: HttpRequest, profile: SearchProfile, user: WorkspaceUser, creating: bool
) -> HttpResponse:
    form = SearchProfileForm(request.POST or None, instance=profile)
    form.instance.user = user
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            saved_profile = form.save()
        refresh_profile_matches(profile=saved_profile)
        messages.success(
            request, "Created search profile." if creating else "Updated search profile."
        )
        return redirect("jobs:profile_list")
    return render(
        request,
        "jobs/profile_form.html",
        {"form": form, "creating": creating, "profile": profile},
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
