import datetime as dt
import zoneinfo

import pytest
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView

from apps.accounts.models import Profile
from apps.core.mixins import OwnerQuerysetMixin
from apps.core.services import local_today, user_zone
from apps.dashboard.services import build_dashboard_summary, greeting_for

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---------- dashboard ----------
def test_dashboard_empty_state_shows_no_fabricated_numbers(auth_client):
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Nothing here is sample data" in html
    assert "coming in phase" in html.lower()
    assert "£" not in html and "$" not in html


def test_dashboard_onboarding_prompt_only_until_completed(auth_client, user):
    assert "Start setup" in auth_client.get(reverse("dashboard:home")).content.decode()
    user.profile.onboarding_completed = True; user.profile.save()
    assert "Start setup" not in auth_client.get(reverse("dashboard:home")).content.decode()


def test_dashboard_greets_by_display_name_and_shows_focus_areas(auth_client, user):
    user.profile.display_name = "Aria"; user.profile.life_areas = ["career", "travel", "bogus"]; user.profile.save()
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Aria" in html and "Career, Travel" in html and "bogus" not in html


def test_summary_falls_back_to_email_prefix_without_display_name(user):
    assert build_dashboard_summary(user)["name"] == "alice"


@pytest.mark.parametrize("hour,expected", [(5, "Good morning"), (11, "Good morning"), (12, "Good afternoon"), (17, "Good afternoon"), (18, "Good evening"), (2, "Good evening")])
def test_greeting_boundaries(hour, expected):
    assert greeting_for(hour) == expected


def test_dashboard_cards_link_to_modules(auth_client):
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    for name in ("core:planner", "core:goals", "core:finances", "core:habits"):
        assert f'href="{reverse(name)}"' in html


# ---------- navigation ----------
NAV = ["dashboard:home", "core:goals", "core:planner", "core:habits", "core:finances",
       "core:future_plans", "core:interests", "core:reviews", "accounts:settings"]


def test_all_nine_nav_destinations_load_and_are_listed(auth_client):
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    for label in ("Dashboard", "My Goals", "Planner", "Habits", "Finances", "Future Plans",
                  "Hobbies &amp; Bucket List", "Reviews &amp; Insights", "Settings"):
        assert label in html
    for name in NAV:
        assert auth_client.get(reverse(name)).status_code == 200


def test_mobile_bottom_nav_and_drawer_present(auth_client):
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    assert 'class="btm-nav' in html and 'class="drawer lg:drawer-open"' in html
    assert 'aria-label="Main navigation"' in html


def test_current_page_marked_active(auth_client):
    html = auth_client.get(reverse("core:goals")).content.decode()
    assert 'aria-current="page"' in html


def test_placeholder_pages_say_they_are_not_built(auth_client):
    html = auth_client.get(reverse("core:finances")).content.decode()
    assert "isn't built yet" in html and "Phase 3" in html


def test_quick_add_entries_are_disabled_not_fake(auth_client):
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Available soon" in html and 'class="disabled"' in html


@override_settings(SITE_NAME="Atlas")
def test_site_name_is_configurable(auth_client):
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Atlas" in html and "<title>Dashboard · Atlas</title>" in html


def test_auth_pages_load_when_anonymous(client):
    for name in ("accounts:login", "accounts:register", "accounts:password_reset", "accounts:password_reset_done", "accounts:password_reset_complete"):
        assert client.get(reverse(name)).status_code == 200


def test_forms_render_labels_for_accessibility(client):
    html = client.get(reverse("accounts:register")).content.decode()
    assert html.count("<label") >= 3 and 'for="id_email"' in html


# ---------- timezone handling ----------
def test_user_zone_uses_profile_and_falls_back_to_utc(user):
    user.profile.timezone = "Asia/Kathmandu"; user.profile.save()
    assert user_zone(user) == zoneinfo.ZoneInfo("Asia/Kathmandu")
    Profile.objects.filter(pk=user.profile.pk).update(timezone="Not/AZone")
    user.profile.refresh_from_db()
    assert user_zone(user) == zoneinfo.ZoneInfo("UTC")


def test_local_today_differs_across_timezones_at_same_instant(user, monkeypatch):
    instant = dt.datetime(2026, 3, 1, 23, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(timezone, "now", lambda: instant)
    user.profile.timezone = "Pacific/Auckland"; user.profile.save()
    assert local_today(user) == dt.date(2026, 3, 2)   # already tomorrow in NZ
    user.profile.timezone = "America/Los_Angeles"; user.profile.save()
    assert local_today(user) == dt.date(2026, 3, 1)


def test_dst_boundary_uses_local_date(user, monkeypatch):
    # 2026-03-29 00:30 UTC is 01:30 GMT, 31 min after clocks would jump at 01:00 UTC
    monkeypatch.setattr(timezone, "now", lambda: dt.datetime(2026, 3, 29, 0, 59, tzinfo=dt.timezone.utc))
    assert local_today(user) == dt.date(2026, 3, 29)


def test_middleware_activates_user_timezone_during_request(auth_client, user):
    user.profile.timezone = "Asia/Tokyo"; user.profile.save()
    seen = {}
    from apps.dashboard import views
    original = views.render

    def spy(*a, **kw):
        seen["tz"] = str(timezone.get_current_timezone())
        return original(*a, **kw)

    views.render = spy
    try:
        auth_client.get(reverse("dashboard:home"))
    finally:
        views.render = original
    assert seen["tz"] == "Asia/Tokyo"
    assert str(timezone.get_current_timezone()) == "UTC"   # reset after request


# ---------- ownership mixin (applies to every owned model in later phases) ----------
class _ProfileDetail(OwnerQuerysetMixin, DetailView):
    model = Profile
    owner_field = "user"


def _get(user, pk):
    req = RequestFactory().get("/")
    req.user = user
    return _ProfileDetail.as_view()(req, pk=pk)


def test_owner_mixin_returns_own_object(user):
    assert _get(user, user.profile.pk).status_code == 200


def test_owner_mixin_404s_for_other_users_object(user, other_user):
    with pytest.raises(Http404):
        _get(user, other_user.profile.pk)


def test_owner_mixin_redirects_anonymous(other_user):
    from django.contrib.auth.models import AnonymousUser
    assert _get(AnonymousUser(), other_user.profile.pk).status_code == 302
