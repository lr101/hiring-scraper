from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/select/", views.select_user, name="select_user"),
    path("accounts/", views.account_list, name="account_list"),
]
