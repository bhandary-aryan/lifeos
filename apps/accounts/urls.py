from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.SignInView.as_view(), name="login"),
    path("logout/", views.sign_out, name="logout"),
    path("onboarding/<int:step>/", views.onboarding, name="onboarding"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/theme/", views.set_theme, name="set_theme"),
    path("settings/export/", views.export_data, name="export"),
    path("settings/export/<str:kind>.csv", views.export_csv, name="export_csv"),
    path("settings/delete/", views.delete_account_view, name="delete"),
    path(
        "password-reset/",
        views.ThrottledPasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            email_template_name="accounts/password_reset_email.txt",
            subject_template_name="accounts/password_reset_subject.txt",
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password_reset_complete",
    ),
]
