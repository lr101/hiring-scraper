from django.db import migrations, models


def _canonical_domain(value: str) -> str:
    domain = value.strip().casefold().rstrip(".")
    return domain[4:] if domain.startswith("www.") else domain


def _merge_job(*, duplicate, survivor, apps) -> None:  # type: ignore[no-untyped-def]
    UserJobState = apps.get_model("jobs", "UserJobState")
    JobMatch = apps.get_model("jobs", "JobMatch")
    for state in UserJobState.objects.filter(job_id=duplicate.pk).order_by("pk"):
        survivor_state = UserJobState.objects.filter(
            job_id=survivor.pk, user_id=state.user_id
        ).first()
        if survivor_state is None:
            state.job_id = survivor.pk
            state.save(update_fields=["job"])
            continue
        update_fields = []
        if survivor_state.status == "none" and state.status != "none":
            survivor_state.status = state.status
            update_fields.append("status")
        if not survivor_state.notes and state.notes:
            survivor_state.notes = state.notes
            update_fields.append("notes")
        if survivor_state.seen_at is None and state.seen_at is not None:
            survivor_state.seen_at = state.seen_at
            update_fields.append("seen_at")
        if update_fields:
            survivor_state.save(update_fields=update_fields)
        state.delete()
    for match in JobMatch.objects.filter(job_id=duplicate.pk).order_by("pk"):
        survivor_match = JobMatch.objects.filter(
            job_id=survivor.pk, profile_id=match.profile_id
        ).first()
        if survivor_match is None:
            match.job_id = survivor.pk
            match.save(update_fields=["job"])
            continue
        if match.score > survivor_match.score:
            survivor_match.score = match.score
            survivor_match.explanation = match.explanation
            survivor_match.save(update_fields=["score", "explanation"])
        match.delete()


def _merge_source(*, duplicate, survivor, apps) -> None:  # type: ignore[no-untyped-def]
    CrawlRun = apps.get_model("jobs", "CrawlRun")
    Job = apps.get_model("jobs", "Job")
    for run in CrawlRun.objects.filter(source_id=duplicate.pk).order_by("pk"):
        run.source_id = survivor.pk
        run.save(update_fields=["source"])
    for job in Job.objects.filter(source_id=duplicate.pk).order_by("pk"):
        survivor_job = Job.objects.filter(
            source_id=survivor.pk, external_id=job.external_id
        ).first()
        if survivor_job is None:
            job.source_id = survivor.pk
            job.save(update_fields=["source"])
        else:
            _merge_job(duplicate=job, survivor=survivor_job, apps=apps)
            job.delete()
    duplicate.delete()


def _merge_company(*, duplicate, survivor, apps) -> None:  # type: ignore[no-untyped-def]
    MonitoringTarget = apps.get_model("jobs", "MonitoringTarget")
    CareerSource = apps.get_model("jobs", "CareerSource")
    for target in MonitoringTarget.objects.filter(company_id=duplicate.pk).order_by("pk"):
        already_targeted = MonitoringTarget.objects.filter(
            user_id=target.user_id,
            kind="company",
            company_id=survivor.pk,
        ).exists()
        if already_targeted:
            target.delete()
        else:
            target.company_id = survivor.pk
            target.save(update_fields=["company"])
    for source in list(CareerSource.objects.filter(company_id=duplicate.pk).order_by("pk")):
        existing_source = (
            CareerSource.objects.filter(source_url=source.source_url)
            .exclude(pk=source.pk)
            .order_by("pk")
            .first()
        )
        if existing_source is None:
            source.company_id = survivor.pk
            source.save(update_fields=["company"])
        else:
            _merge_source(duplicate=source, survivor=existing_source, apps=apps)
    duplicate.delete()


def merge_duplicate_company_domains(apps, schema_editor) -> None:  # type: ignore[no-untyped-def]
    """Keep the oldest company for each normalized domain and rehome its related data."""
    Company = apps.get_model("jobs", "Company")
    survivors = {}
    for company in Company.objects.order_by("pk").iterator():
        domain = _canonical_domain(company.domain)
        survivor = survivors.get(domain)
        if survivor is None:
            survivors[domain] = company
            if company.domain != domain:
                company.domain = domain
                company.save(update_fields=["domain"])
            continue
        _merge_company(duplicate=company, survivor=survivor, apps=apps)


class Migration(migrations.Migration):
    dependencies = [("jobs", "0006_monitoring_targets")]

    operations = [
        migrations.RunPython(merge_duplicate_company_domains, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="company",
            name="domain",
            field=models.CharField(max_length=253, unique=True),
        ),
    ]
