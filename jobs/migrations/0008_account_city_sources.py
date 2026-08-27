from django.db import migrations


def move_profile_locations_to_account_targets(apps, schema_editor) -> None:  # type: ignore[no-untyped-def]
    ProfileLocation = apps.get_model("jobs", "ProfileLocation")
    MonitoringTarget = apps.get_model("jobs", "MonitoringTarget")
    for location in ProfileLocation.objects.select_related("profile").order_by("pk").iterator():
        target, created = MonitoringTarget.objects.get_or_create(
            user_id=location.profile.user_id,
            kind="city",
            place_id=location.place_id,
            defaults={"radius_km": location.radius_km},
        )
        if not created and target.radius_km < location.radius_km:
            target.radius_km = location.radius_km
            target.save(update_fields=["radius_km"])


class Migration(migrations.Migration):
    dependencies = [("jobs", "0007_dynamic_company_domains")]

    operations = [
        migrations.RunPython(move_profile_locations_to_account_targets, migrations.RunPython.noop),
        migrations.DeleteModel(name="ProfileLocation"),
    ]
