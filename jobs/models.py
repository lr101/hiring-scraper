from typing import Any

from django.db import models

from .exclusions import normalize_exclusion_pattern


class WorkspaceUser(models.Model):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_selected_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name


class Company(models.Model):
    name = models.CharField(max_length=200)
    domain = models.CharField(max_length=253, unique=True)
    career_url = models.URLField(max_length=1000, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "companies"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CareerSource(models.Model):
    class Kind(models.TextChoices):
        CUSTOM = "custom", "Custom"
        GREENHOUSE = "greenhouse", "Greenhouse"
        LEVER = "lever", "Lever"
        PERSONIO = "personio", "Personio"
        SOFTGARDEN = "softgarden", "Softgarden"
        WORKDAY = "workday", "Workday"
        SUCCESSFACTORS = "successfactors", "SAP SuccessFactors"
        JSON_LD = "json_ld", "JSON-LD"
        SIEMENS_AVATURE = "siemens_avature", "Siemens Avature"
        BOSCH_SMARTRECRUITERS = "bosch_smartrecruiters", "Bosch SmartRecruiters"
        SAP_SUCCESSFACTORS = "sap_successfactors", "SAP SuccessFactors Jobs2Web"
        TELEKOM_JSON = "telekom_json", "Deutsche Telekom JSON"
        DHL_PHENOM = "dhl_phenom", "DHL Phenom"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.CUSTOM)
    source_url = models.URLField(max_length=1000, unique=True)
    tenant = models.CharField(max_length=200, blank=True)
    config = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)
    request_delay_seconds = models.PositiveSmallIntegerField(default=2)
    max_pages = models.PositiveSmallIntegerField(default=100)
    blocked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    last_job_count = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.company}: {self.get_kind_display()}"


class Job(models.Model):
    class RemoteType(models.TextChoices):
        ONSITE = "onsite", "On-site"
        HYBRID = "hybrid", "Hybrid"
        REMOTE = "remote", "Remote"
        UNKNOWN = "unknown", "Unknown"

    source = models.ForeignKey(CareerSource, on_delete=models.CASCADE, related_name="jobs")
    external_id = models.CharField(max_length=300)
    canonical_url = models.URLField(max_length=1500)
    application_url = models.URLField(max_length=1500, blank=True)
    title = models.CharField(max_length=500)
    normalized_title = models.CharField(max_length=500, db_index=True)
    description_html = models.TextField(blank=True)
    description_text = models.TextField(blank=True)
    city = models.CharField(max_length=200, blank=True, db_index=True)
    state = models.CharField(max_length=200, blank=True)
    country_code = models.CharField(max_length=2, default="DE")
    locations = models.JSONField(default=list, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    remote_type = models.CharField(
        max_length=16, choices=RemoteType.choices, default=RemoteType.UNKNOWN
    )
    employment_type = models.CharField(max_length=100, blank=True)
    seniority = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=200, blank=True)
    industry = models.CharField(max_length=200, blank=True)
    salary_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_currency = models.CharField(max_length=3, default="EUR")
    language_requirement = models.CharField(max_length=100, blank=True)
    skills = models.JSONField(default=list, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    missed_runs = models.PositiveSmallIntegerField(default=0)
    content_hash = models.CharField(max_length=64, db_index=True)
    fingerprint = models.CharField(max_length=64, db_index=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="unique_job_per_source")
        ]
        ordering = ["-first_seen_at"]

    def __str__(self) -> str:
        return f"{self.title} at {self.source.company}"

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


class SearchProfile(models.Model):
    user = models.ForeignKey(WorkspaceUser, on_delete=models.CASCADE, related_name="profiles")
    name = models.CharField(max_length=200)
    is_enabled = models.BooleanField(default=True)
    include_remote = models.BooleanField(default=True)
    included_titles = models.JSONField(default=list, blank=True)
    excluded_titles = models.JSONField(default=list, blank=True)
    required_skill_groups = models.JSONField(default=list, blank=True)
    preferred_skills = models.JSONField(default=list, blank=True)
    excluded_skills = models.JSONField(default=list, blank=True)
    employment_types = models.JSONField(default=list, blank=True)
    departments = models.JSONField(default=list, blank=True)
    industries = models.JSONField(default=list, blank=True)
    seniority_levels = models.JSONField(default=list, blank=True)
    minimum_salary = models.PositiveIntegerField(null=True, blank=True)
    maximum_german_level = models.CharField(max_length=4, blank=True)
    weights = models.JSONField(default=dict, blank=True)
    minimum_score = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_profile_name_per_user")
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class GermanPlace(models.Model):
    """A cached location-provider result that can anchor a profile radius."""

    class SourceKind(models.TextChoices):
        CITY = "city", "City"
        POSTAL_CODE = "postal_code", "Postal code"

    source_id = models.CharField(max_length=300, unique=True)
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200, db_index=True)
    postal_code = models.CharField(max_length=12, blank=True, db_index=True)
    admin_area = models.CharField(max_length=200, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    population = models.PositiveIntegerField(null=True, blank=True)
    source_kind = models.CharField(max_length=16, choices=SourceKind.choices)
    source_snapshot = models.CharField(max_length=32, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_kind", "postal_code", "normalized_name", "latitude", "longitude"],
                name="unique_imported_german_place",
            )
        ]
        ordering = ["name", "admin_area", "postal_code"]

    def __str__(self) -> str:
        details = ", ".join(part for part in [self.postal_code, self.admin_area] if part)
        return f"{self.name} ({details})" if details else self.name


class ProfileLocation(models.Model):
    profile = models.ForeignKey(
        SearchProfile, on_delete=models.CASCADE, related_name="profile_locations"
    )
    place = models.ForeignKey(
        GermanPlace, on_delete=models.PROTECT, related_name="profile_locations"
    )
    city = models.CharField(max_length=200)
    latitude = models.FloatField()
    longitude = models.FloatField()
    radius_km = models.PositiveSmallIntegerField(default=25)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profile", "place"], name="unique_place_per_profile")
        ]

    def __str__(self) -> str:
        return f"{self.city} ({self.radius_km} km)"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.city = self.place.name
        self.latitude = self.place.latitude
        self.longitude = self.place.longitude
        super().save(*args, **kwargs)


class MonitoringTarget(models.Model):
    class Kind(models.TextChoices):
        COMPANY = "company", "Company"
        CITY = "city", "City"

    user = models.ForeignKey(
        WorkspaceUser, on_delete=models.CASCADE, related_name="monitoring_targets"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="monitoring_targets",
        null=True,
        blank=True,
    )
    place = models.ForeignKey(
        GermanPlace,
        on_delete=models.PROTECT,
        related_name="monitoring_targets",
        null=True,
        blank=True,
    )
    radius_km = models.PositiveSmallIntegerField(default=25)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(kind="company", company__isnull=False, place__isnull=True)
                    | models.Q(kind="city", company__isnull=True, place__isnull=False)
                ),
                name="monitoring_target_has_selection",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="company") | models.Q(kind="city", radius_km__gt=0),
                name="city_monitoring_target_has_radius",
            ),
            models.UniqueConstraint(
                fields=["user", "company"],
                condition=models.Q(kind="company"),
                name="unique_company_monitoring_target",
            ),
            models.UniqueConstraint(
                fields=["user", "place"],
                condition=models.Q(kind="city"),
                name="unique_city_monitoring_target",
            ),
        ]

    def __str__(self) -> str:
        if self.kind == self.Kind.COMPANY:
            return str(self.company)
        return f"{self.place} ({self.radius_km} km)"


class JobMatch(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="matches")
    profile = models.ForeignKey(SearchProfile, on_delete=models.CASCADE, related_name="matches")
    score = models.SmallIntegerField()
    explanation = models.JSONField(default=dict)
    is_rejected = models.BooleanField(default=False)
    first_matched_at = models.DateTimeField(auto_now_add=True)
    last_evaluated_at = models.DateTimeField(auto_now=True)
    is_new = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["job", "profile"], name="unique_job_profile_match")
        ]
        ordering = ["-score", "-first_matched_at"]

    def __str__(self) -> str:
        return f"{self.job} for {self.profile}"


class UserJobState(models.Model):
    class Status(models.TextChoices):
        NONE = "none", "No status"
        SAVED = "saved", "Saved"
        IGNORED = "ignored", "Ignored"
        APPLIED = "applied", "Applied"
        INTERVIEWING = "interviewing", "Interviewing"
        REJECTED = "rejected", "Rejected"
        OFFER = "offer", "Offer"

    user = models.ForeignKey(WorkspaceUser, on_delete=models.CASCADE, related_name="job_states")
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="user_states")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NONE)
    seen_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "job"], name="unique_job_state_per_user")
        ]

    def __str__(self) -> str:
        return f"{self.user}: {self.job} ({self.get_status_display()})"


class ExclusionRule(models.Model):
    class Kind(models.TextChoices):
        COMPANY = "company", "Company"
        WEBSITE = "website", "Website"
        TITLE = "title", "Title"
        SKILL = "skill", "Skill"

    user = models.ForeignKey(WorkspaceUser, on_delete=models.CASCADE, related_name="exclusions")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    pattern = models.CharField(max_length=500)
    normalized_pattern = models.CharField(max_length=500)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind", "normalized_pattern"], name="unique_exclusion_per_user"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user}: {self.get_kind_display()} {self.pattern}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.pattern = " ".join(self.pattern.split())
        self.normalized_pattern = normalize_exclusion_pattern(self.pattern)
        super().save(*args, **kwargs)


class CrawlRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        BLOCKED = "blocked", "Blocked"

    source = models.ForeignKey(CareerSource, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    jobs_seen = models.PositiveIntegerField(default=0)
    jobs_created = models.PositiveIntegerField(default=0)
    jobs_updated = models.PositiveIntegerField(default=0)
    jobs_closed = models.PositiveIntegerField(default=0)
    requests_made = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=models.Q(status="running"),
                name="one_running_crawl_per_source",
            )
        ]
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.source} at {self.started_at:%Y-%m-%d %H:%M}"
