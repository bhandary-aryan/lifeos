from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("reminders/", views.reminders, name="reminders"),
    path("reminders/badge/", views.reminders_badge, name="reminders_badge"),
]
