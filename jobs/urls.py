from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("", views.home, name="home"),
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
]
