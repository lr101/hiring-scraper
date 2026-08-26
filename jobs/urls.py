from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("", views.home, name="home"),
    path("jobs/", views.job_list, name="job_list"),
    path("jobs/<int:job_id>/", views.job_detail, name="job_detail"),
    path("jobs/<int:job_id>/state/", views.job_state, name="job_state"),
    path("companies/", views.company_list, name="company_list"),
    path("companies/<int:company_id>/", views.company_detail, name="company_detail"),
    path("sources/", views.source_list, name="source_list"),
    path("sources/<int:source_id>/toggle/", views.source_toggle, name="source_toggle"),
    path("sources/<int:source_id>/unblock/", views.source_unblock, name="source_unblock"),
    path("sources/<int:source_id>/run/", views.source_run, name="source_run"),
    path("runs/", views.run_list, name="run_list"),
    path("accounts/select/", views.select_user, name="select_user"),
    path("accounts/", views.account_list, name="account_list"),
    path("profiles/", views.profile_list, name="profile_list"),
    path("profiles/new/", views.profile_create, name="profile_create"),
    path("profiles/<int:profile_id>/edit/", views.profile_edit, name="profile_edit"),
    path("profiles/<int:profile_id>/delete/", views.profile_delete, name="profile_delete"),
    path("exclusions/", views.exclusion_list, name="exclusion_list"),
    path("exclusions/new/", views.exclusion_create, name="exclusion_create"),
    path("exclusions/<int:rule_id>/toggle/", views.exclusion_toggle, name="exclusion_toggle"),
    path("exclusions/<int:rule_id>/delete/", views.exclusion_delete, name="exclusion_delete"),
    path("places/search/", views.place_search, name="place_search"),
    path("places/search.json", views.place_search_json, name="place_search_json"),
]
