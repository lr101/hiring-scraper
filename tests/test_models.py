from django.apps import apps


def test_core_domain_models_are_registered() -> None:
    expected = {
        "WorkspaceUser",
        "Company",
        "CareerSource",
        "Job",
        "SearchProfile",
        "ProfileLocation",
        "JobMatch",
        "UserJobState",
        "ExclusionRule",
        "CrawlRun",
    }
    registered = {model.__name__ for model in apps.get_app_config("jobs").get_models()}

    assert expected <= registered
