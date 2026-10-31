import json

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts.models import Profile

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------- user model ----------
def test_create_user_normalises_email_and_hashes_password(password):
    u = User.objects.create_user("MiXed@Example.COM", password)
    assert u.email == "mixed@example.com"
    assert u.password != password and u.check_password(password)


def test_email_unique_case_insensitively(user):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(email="ALICE@example.com", password="x")


def test_profile_created_automatically_with_defaults(user):
    p = user.profile
    assert (p.currency, p.timezone, p.week_start, p.theme) == ("GBP", "Europe/London", 0, "system")
    assert p.onboarding_completed is False


def test_week_start_check_constraint(user):
    with pytest.raises(IntegrityError), transaction.atomic():
        Profile.objects.filter(pk=user.profile.pk).update(week_start=9)


def test_superuser_flags(password):
    su = User.objects.create_superuser("root@example.com", password)
    assert su.is_staff and su.is_superuser


# ---------- registration / login / logout ----------
def test_register_creates_user_logs_in_and_starts_onboarding(client, password):
    r = client.post(reverse("accounts:register"), {"email": "New@Example.com", "password1": password, "password2": password})
    assert r.status_code == 302 and r.url == reverse("accounts:onboarding", kwargs={"step": 1})
    u = User.objects.get(email="new@example.com")
    assert u.profile and "_auth_user_id" in client.session


def test_register_rejects_duplicate_email_any_case(client, user, password):
    r = client.post(reverse("accounts:register"), {"email": "ALICE@example.com", "password1": password, "password2": password})
    assert r.status_code == 200 and "already exists" in r.content.decode()
    assert User.objects.count() == 1


def test_register_rejects_mismatch_and_weak_passwords(client):
    r = client.post(reverse("accounts:register"), {"email": "a@b.com", "password1": "Different-pass-1!", "password2": "nope"})
    assert "do not match" in r.content.decode()
    r = client.post(reverse("accounts:register"), {"email": "a@b.com", "password1": "12345678", "password2": "12345678"})
    assert r.status_code == 200 and not User.objects.filter(email="a@b.com").exists()


def test_login_success_and_failure(client, user, password):
    bad = client.post(reverse("accounts:login"), {"username": user.email, "password": "wrong"})
    assert bad.status_code == 200 and "_auth_user_id" not in client.session
    ok = client.post(reverse("accounts:login"), {"username": "ALICE@example.com", "password": password})
    assert ok.status_code == 302 and ok.url == reverse("dashboard:home")


def test_logout_requires_post_and_ends_session(auth_client):
    assert auth_client.get(reverse("accounts:logout")).status_code == 405
    r = auth_client.post(reverse("accounts:logout"))
    assert r.status_code == 302 and "_auth_user_id" not in auth_client.session


# ---------- anonymous access ----------
@pytest.mark.parametrize("name", [
    "dashboard:home", "accounts:settings", "accounts:export", "accounts:delete",
    "goals:list", "planner:today", "planner:habits", "finance:overview",
    "future_plans:list", "interests:list", "reflections:list",
])
def test_private_pages_redirect_anonymous(client, name):
    r = client.get(reverse(name))
    assert r.status_code == 302 and r.url.startswith(reverse("accounts:login"))


def test_anonymous_cannot_post_theme_or_onboarding(client):
    assert client.post(reverse("accounts:set_theme"), {"theme": "dark"}).status_code == 302
    assert client.get(reverse("accounts:onboarding", kwargs={"step": 1})).status_code == 302


def test_csrf_enforced_on_state_changing_requests(user):
    from django.test import Client
    c = Client(enforce_csrf_checks=True)
    c.force_login(user)
    assert c.post(reverse("accounts:set_theme"), {"theme": "dark"}).status_code == 403
    assert c.post(reverse("accounts:logout")).status_code == 403


# ---------- onboarding ----------
def test_onboarding_saves_each_step_and_completes(auth_client, user):
    auth_client.post(reverse("accounts:onboarding", kwargs={"step": 1}), {"display_name": "Ali"})
    auth_client.post(reverse("accounts:onboarding", kwargs={"step": 2}), {
        "currency": "NPR", "timezone": "Asia/Kathmandu", "date_format": "Y-m-d", "week_start": "6", "daily_available_minutes": "300", "reminders_enabled": "on", "reminder_lead_days": "3", "theme": "dark"})
    r = auth_client.post(reverse("accounts:onboarding", kwargs={"step": 3}), {"life_areas": ["career", "health"]})
    user.profile.refresh_from_db()
    p = user.profile
    assert (p.display_name, p.currency, p.timezone, p.week_start, p.theme) == ("Ali", "NPR", "Asia/Kathmandu", 6, "dark")
    assert p.life_areas == ["career", "health"] and p.onboarding_completed
    assert r.url == reverse("dashboard:home")


def test_onboarding_can_be_skipped_entirely(auth_client, user):
    for step in (1, 2):
        r = auth_client.post(reverse("accounts:onboarding", kwargs={"step": step}), {"skip": "1"})
        assert r.url == reverse("accounts:onboarding", kwargs={"step": step + 1})
    auth_client.post(reverse("accounts:onboarding", kwargs={"step": 3}), {"skip": "1"})
    user.profile.refresh_from_db()
    assert user.profile.onboarding_completed and user.profile.display_name == ""


def test_onboarding_rejects_invalid_values(auth_client, user):
    r = auth_client.post(reverse("accounts:onboarding", kwargs={"step": 2}), {
        "currency": "XXX", "timezone": "Mars/Base", "date_format": "Y-m-d", "week_start": "3", "daily_available_minutes": "300", "reminders_enabled": "on", "reminder_lead_days": "3", "theme": "dark"})
    assert r.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.currency == "GBP"


def test_unknown_onboarding_step_redirects(auth_client):
    assert auth_client.get(reverse("accounts:onboarding", kwargs={"step": 99})).status_code == 302


# ---------- settings / theme ----------
def test_settings_page_and_updates(auth_client, user):
    assert auth_client.get(reverse("accounts:settings")).status_code == 200
    auth_client.post(reverse("accounts:settings"), {"section": "profile", "profile-display_name": "Alice"})
    auth_client.post(reverse("accounts:settings"), {
        "section": "prefs", "prefs-currency": "USD", "prefs-timezone": "America/New_York",
        "prefs-date_format": "m/d/Y", "prefs-week_start": "6", "prefs-daily_available_minutes": "360", "prefs-reminders_enabled": "on", "prefs-reminder_lead_days": "3", "prefs-theme": "light"})
    user.profile.refresh_from_db()
    assert (user.profile.display_name, user.profile.currency, user.profile.timezone) == ("Alice", "USD", "America/New_York")
    assert user.profile.daily_available_minutes == 360


def test_reminder_preferences_saved_and_validated(auth_client, user):
    base = {"section": "prefs", "prefs-currency": "GBP", "prefs-timezone": "UTC", "prefs-date_format": "d/m/Y", "prefs-week_start": "0",
            "prefs-daily_available_minutes": "480", "prefs-theme": "light"}
    auth_client.post(reverse("accounts:settings"), {**base, "prefs-reminder_lead_days": "7"})          # checkbox absent => off
    user.profile.refresh_from_db()
    assert user.profile.reminders_enabled is False and user.profile.reminder_lead_days == 7
    for bad in ("0", "15", "abc", ""):
        r = auth_client.post(reverse("accounts:settings"), {**base, "prefs-reminders_enabled": "on", "prefs-reminder_lead_days": bad})
        assert r.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.reminder_lead_days == 7 and user.profile.reminders_enabled is False


def test_available_minutes_validated(auth_client, user):
    for bad in ("0", "5000", "abc"):
        r = auth_client.post(reverse("accounts:settings"), {
            "section": "prefs", "prefs-currency": "GBP", "prefs-timezone": "UTC", "prefs-date_format": "d/m/Y",
            "prefs-week_start": "0", "prefs-daily_available_minutes": bad, "prefs-reminders_enabled": "on", "prefs-reminder_lead_days": "3", "prefs-theme": "light"})
        assert r.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.daily_available_minutes == 480


def test_settings_invalid_prefs_show_errors_and_do_not_save(auth_client, user):
    r = auth_client.post(reverse("accounts:settings"), {
        "section": "prefs", "prefs-currency": "ZZZ", "prefs-timezone": "UTC",
        "prefs-date_format": "m/d/Y", "prefs-week_start": "6", "prefs-daily_available_minutes": "360", "prefs-reminders_enabled": "on", "prefs-reminder_lead_days": "3", "prefs-theme": "light"})
    assert r.status_code == 200 and "Select a valid choice" in r.content.decode()
    user.profile.refresh_from_db()
    assert user.profile.currency == "GBP"


def test_set_theme_valid_and_invalid(auth_client, user):
    assert auth_client.post(reverse("accounts:set_theme"), {"theme": "dark"}).status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.theme == "dark"
    assert auth_client.post(reverse("accounts:set_theme"), {"theme": "neon"}).status_code == 400
    assert auth_client.get(reverse("accounts:set_theme")).status_code == 405


def test_theme_preference_rendered_into_page(auth_client, user):
    user.profile.theme = "dark"; user.profile.save()
    assert 'var pref = "dark"' in auth_client.get(reverse("dashboard:home")).content.decode()


# ---------- export / delete / isolation ----------
def test_export_contains_only_own_data(auth_client, user, other_user):
    other_user.profile.display_name = "BobSecret"; other_user.profile.save()
    r = auth_client.get(reverse("accounts:export"))
    data = json.loads(r.content)
    assert r.status_code == 200 and "attachment" in r["Content-Disposition"]
    assert data["account"]["email"] == user.email
    assert "BobSecret" not in r.content.decode() and "bob@example.com" not in r.content.decode()
    assert "password" not in r.content.decode().lower()


def test_delete_account_needs_correct_password_and_confirmation(auth_client, user, password):
    url = reverse("accounts:delete")
    r = auth_client.post(url, {"password": "wrong", "confirm": "on"})
    assert r.status_code == 200 and User.objects.filter(pk=user.pk).exists()
    r = auth_client.post(url, {"password": password})
    assert r.status_code == 200 and User.objects.filter(pk=user.pk).exists()
    r = auth_client.post(url, {"password": password, "confirm": "on"})
    assert r.status_code == 302
    assert not User.objects.filter(pk=user.pk).exists()
    assert not Profile.objects.filter(user_id=user.pk).exists()


def test_deleting_one_account_leaves_others_intact(client, user, other_user, password):
    client.force_login(user)
    client.post(reverse("accounts:delete"), {"password": password, "confirm": "on"})
    assert User.objects.filter(pk=other_user.pk).exists() and Profile.objects.filter(user=other_user).exists()


def test_settings_changes_only_affect_the_signed_in_user(auth_client, user, other_user):
    auth_client.post(reverse("accounts:set_theme"), {"theme": "dark"})
    other_user.profile.refresh_from_db()
    assert other_user.profile.theme == "system"


# ---------- password reset ----------
def test_password_reset_flow_sends_email_and_hides_account_existence(client, user):
    r = client.post(reverse("accounts:password_reset"), {"email": user.email})
    assert r.status_code == 302 and len(mail.outbox) == 1
    assert "/accounts/reset/" in mail.outbox[0].body
    client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
    assert len(mail.outbox) == 1  # no email, but same response
