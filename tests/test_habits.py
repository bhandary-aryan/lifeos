import datetime as dt

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.services import local_today
from apps.planner import habit_services as hs
from apps.planner.forms import HabitForm
from apps.planner.models import Habit, HabitLog

D = dt.timedelta
d = dt.date
MON = d(2026, 3, 2)     # a Monday
H = Habit.Frequency


def habit(freq=H.DAILY, start=d(2026, 1, 1), **kw):
    return Habit(name="h", frequency=freq, start_date=start, **kw)


def run(start, n):
    return {start + D(days=i) for i in range(n)}


# ---------- pure service maths (no DB) ----------
def test_daily_streak_counts_consecutive_days_including_today():
    today = d(2026, 3, 10)
    assert hs.current_streak(habit(), run(d(2026, 3, 8), 3), today) == 3


def test_unlogged_today_does_not_break_streak():
    today = d(2026, 3, 10)
    assert hs.current_streak(habit(), run(d(2026, 3, 7), 3), today) == 3     # 7,8,9 logged; today pending


def test_missed_day_breaks_current_streak_but_not_best():
    today = d(2026, 3, 10)
    logs = run(d(2026, 3, 1), 5) | {d(2026, 3, 9), d(2026, 3, 10)}     # 1-5, miss 6-8, then 9-10
    assert hs.current_streak(habit(), logs, today) == 2
    assert hs.best_streak(habit(), logs, today) == 5


def test_no_logs_zero_streaks():
    assert hs.current_streak(habit(), set(), d(2026, 3, 10)) == 0
    assert hs.best_streak(habit(), set(), d(2026, 3, 10)) == 0


def test_streak_not_started_yet_or_before_start():
    assert hs.current_streak(habit(start=d(2026, 5, 1)), set(), d(2026, 3, 10)) == 0
    assert hs.window(habit(start=d(2026, 5, 1)), d(2026, 3, 10)) is None


def test_selected_weekdays_skip_unscheduled_days():
    h = habit(H.WEEKDAYS, weekdays=[0, 2, 4])       # Mon Wed Fri
    logs = {MON, MON + D(days=2), MON + D(days=4), MON + D(days=7)}       # Mon Wed Fri, next Mon
    today = MON + D(days=8)                                               # Tuesday: unscheduled
    assert hs.current_streak(h, logs, today) == 4
    assert hs.scheduled_on(h, MON + D(days=1)) is False


def test_logs_on_unscheduled_days_are_ignored_by_maths():
    h = habit(H.WEEKDAYS, weekdays=[0])
    logs = {MON, MON + D(days=1)}                      # Tue logged but not scheduled
    assert hs.weekly_rate(h, logs, MON, MON + D(days=6), 0) == (1, 1)


def test_weekly_habit_streak_counts_met_weeks_and_in_progress_week_is_free():
    h = habit(H.WEEKLY, weekly_target=2, start=d(2026, 2, 2))
    logs = {d(2026, 2, 2), d(2026, 2, 4), d(2026, 2, 9), d(2026, 2, 12), d(2026, 2, 16)}     # wk1 met, wk2 met, wk3 only 1 so far
    today = d(2026, 2, 18)                                                                  # mid week 3
    assert hs.current_streak(h, logs, today, 0) == 2
    logs |= {d(2026, 2, 17)}
    assert hs.current_streak(h, logs, today, 0) == 3


def test_weekly_habit_unmet_past_week_breaks_streak():
    h = habit(H.WEEKLY, weekly_target=3, start=d(2026, 2, 2))
    logs = {d(2026, 2, 2), d(2026, 2, 3), d(2026, 2, 4), d(2026, 2, 9)}          # week1 met, week2 missed
    assert hs.current_streak(h, logs, d(2026, 2, 17), 0) == 0
    assert hs.best_streak(h, logs, d(2026, 2, 17), 0) == 1


def test_week_boundaries_depend_on_week_start():
    sun = d(2026, 3, 1)
    assert hs.week_bounds(sun, 0) == (d(2026, 2, 23), d(2026, 3, 1))       # Monday-start: Sunday ends the week
    assert hs.week_bounds(sun, 6) == (d(2026, 3, 1), d(2026, 3, 7))        # Sunday-start: Sunday begins it


def test_weekly_rate_daily_partial_current_week():
    h = habit(start=d(2026, 3, 2))
    logs = {d(2026, 3, 2), d(2026, 3, 3)}
    assert hs.weekly_rate(h, logs, d(2026, 3, 4), d(2026, 3, 4), 0) == (2, 3)      # Mon-Wed so far
    assert hs.pct(2, 3) == 67 and hs.pct(0, 0) is None


def test_weekly_rate_for_weekly_target_is_capped():
    h = habit(H.WEEKLY, weekly_target=3, start=d(2026, 3, 2))
    logs = run(d(2026, 3, 2), 5)
    assert hs.weekly_rate(h, logs, d(2026, 3, 4), d(2026, 3, 8), 0) == (3, 3)


@pytest.mark.parametrize("year,month,days", [(2026, 1, 31), (2026, 2, 28), (2028, 2, 29), (2026, 4, 30)])
def test_monthly_consistency_uses_exact_month_boundaries(year, month, days):
    h = habit(start=d(2025, 1, 1))
    logs = {d(year, month, 1), d(year, month, days)}
    prev_day, next_day = d(year, month, 1) - D(days=1), d(year, month, days) + D(days=1)
    logs |= {prev_day, next_day}                                  # neighbours must not leak in
    assert hs.monthly_consistency(h, logs, year, month, d(2030, 1, 1)) == (2, days)


def test_monthly_consistency_only_counts_days_up_to_today():
    h = habit(start=d(2026, 3, 1))
    assert hs.monthly_consistency(h, {d(2026, 3, 1)}, 2026, 3, d(2026, 3, 10)) == (1, 10)


def test_monthly_consistency_starts_at_habit_start():
    h = habit(start=d(2026, 3, 20))
    assert hs.monthly_consistency(h, set(), 2026, 3, d(2026, 3, 31)) == (0, 12)


def test_paused_habit_stops_expecting_days_and_keeps_history():
    h = habit(paused_on=d(2026, 3, 5))
    logs = run(d(2026, 3, 1), 5)                                  # 1-5 done, then paused
    assert hs.window(h, d(2026, 3, 20)) == (d(2026, 1, 1), d(2026, 3, 5))
    assert hs.monthly_consistency(h, logs, 2026, 3, d(2026, 3, 20)) == (5, 5)
    assert hs.best_streak(h, logs, d(2026, 3, 20)) == 5


def test_end_date_caps_window():
    h = habit(end_date=d(2026, 3, 10))
    assert hs.window(h, d(2026, 6, 1))[1] == d(2026, 3, 10)
    assert hs.scheduled_on(h, d(2026, 3, 11)) is False


def test_habit_stats_bundle():
    today = d(2026, 3, 4)                                   # Wednesday
    h = habit(start=d(2026, 3, 2))
    s = hs.habit_stats(h, {d(2026, 3, 2), d(2026, 3, 3)}, today, 0)
    assert (s["current_streak"], s["best_streak"], s["week_done"], s["week_expected"], s["month_done"], s["total_logs"]) == (2, 2, 2, 3, 2, 2)


# ---------- DB / views ----------
pytestmark_db = pytest.mark.django_db


@pytest.mark.django_db
def test_one_log_per_habit_per_date_enforced_by_database(user, make_habit):
    h = make_habit(user)
    HabitLog.objects.create(owner=user, habit=h, date=local_today(user))
    with pytest.raises(IntegrityError), transaction.atomic():
        HabitLog.objects.create(owner=user, habit=h, date=local_today(user))


@pytest.mark.django_db
def test_toggle_logs_then_unlogs_without_duplicates(auth_client, user, make_habit):
    h = make_habit(user)
    today = local_today(user).isoformat()
    url = reverse("planner:habit_log", args=[h.pk])
    auth_client.post(url, {"date": today}); auth_client.post(url, {"date": today})
    assert HabitLog.objects.filter(habit=h).count() == 0       # second click un-did the first
    auth_client.post(url, {"date": today})
    assert HabitLog.objects.filter(habit=h).count() == 1


@pytest.mark.django_db
def test_cannot_log_future_or_pre_start_or_paused(auth_client, user, make_habit):
    today = local_today(user)
    h = make_habit(user, start_date=today - D(days=5))
    url = reverse("planner:habit_log", args=[h.pk])
    auth_client.post(url, {"date": (today + D(days=1)).isoformat()})
    auth_client.post(url, {"date": (today - D(days=6)).isoformat()})
    assert HabitLog.objects.count() == 0
    auth_client.post(reverse("planner:habit_pause", args=[h.pk]))
    auth_client.post(url, {"date": today.isoformat()})
    assert HabitLog.objects.count() == 0


@pytest.mark.django_db
def test_logging_uses_users_local_date_not_server_date(auth_client, user, make_habit, monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: dt.datetime(2026, 3, 1, 23, 30, tzinfo=dt.timezone.utc))
    user.profile.timezone = "Pacific/Auckland"; user.profile.save()          # already 2 March there
    h = make_habit(user, start_date=d(2026, 2, 1))
    auth_client.post(reverse("planner:habit_log", args=[h.pk]))              # no explicit date
    assert HabitLog.objects.get(habit=h).date == d(2026, 3, 2)


@pytest.mark.django_db
def test_pause_and_resume_preserve_history(auth_client, user, make_habit, log_habit):
    h = make_habit(user)
    log_habit(h, local_today(user) - D(days=1), local_today(user) - D(days=2))
    auth_client.post(reverse("planner:habit_pause", args=[h.pk]))
    h.refresh_from_db(); assert not h.is_active and h.paused_on == local_today(user)
    assert HabitLog.objects.filter(habit=h).count() == 2
    auth_client.post(reverse("planner:habit_pause", args=[h.pk]))
    h.refresh_from_db(); assert h.is_active and h.paused_on is None and HabitLog.objects.filter(habit=h).count() == 2


@pytest.mark.django_db
def test_editing_habit_keeps_logs(auth_client, user, make_habit, log_habit):
    h = make_habit(user)
    log_habit(h, local_today(user))
    auth_client.post(reverse("planner:habit_update", args=[h.pk]), {
        "name": "Renamed", "frequency": "weekly", "weekly_target": "4", "start_date": h.start_date.isoformat()})
    h.refresh_from_db()
    assert (h.name, h.frequency, h.weekly_target) == ("Renamed", "weekly", 4) and h.logs.count() == 1


@pytest.mark.django_db
def test_habit_form_validation(user):
    base = {"name": "Run", "start_date": "2026-03-01"}
    assert HabitForm({**base, "frequency": "daily"}, user=user).is_valid()
    assert not HabitForm({**base, "frequency": "weekdays"}, user=user).is_valid()                    # needs days
    assert HabitForm({**base, "frequency": "weekdays", "weekdays": ["0", "4"]}, user=user).is_valid()
    assert not HabitForm({**base, "frequency": "weekly", "weekly_target": "9"}, user=user).is_valid()
    assert not HabitForm({**base, "frequency": "weekly", "weekly_target": "0"}, user=user).is_valid()
    assert not HabitForm({**base, "frequency": "daily", "end_date": "2026-02-01"}, user=user).is_valid()


@pytest.mark.django_db
def test_create_weekday_habit_stores_sorted_days(auth_client, user):
    auth_client.post(reverse("planner:habit_create"), {
        "name": "Gym", "frequency": "weekdays", "weekdays": ["4", "0", "2"], "weekly_target": "3", "start_date": "2026-03-01"})
    assert Habit.objects.get(owner=user).weekdays == [0, 2, 4]


@pytest.mark.django_db
def test_daily_habit_clears_stale_weekdays(auth_client, user, make_habit):
    h = make_habit(user, frequency="weekdays", weekdays=[1])
    auth_client.post(reverse("planner:habit_update", args=[h.pk]), {"name": "x", "frequency": "daily", "weekly_target": "3", "start_date": h.start_date.isoformat()})
    h.refresh_from_db(); assert h.weekdays == []


@pytest.mark.django_db
def test_delete_habit_cascades_logs_after_confirmation(auth_client, user, make_habit, log_habit):
    h = make_habit(user); log_habit(h, local_today(user))
    assert auth_client.get(reverse("planner:habit_delete", args=[h.pk])).status_code == 200
    assert Habit.objects.filter(pk=h.pk).exists()
    auth_client.post(reverse("planner:habit_delete", args=[h.pk]))
    assert not Habit.objects.exists() and not HabitLog.objects.exists()


@pytest.mark.django_db
def test_tracker_and_detail_pages_render_with_real_stats(auth_client, user, make_habit, log_habit):
    today = local_today(user)
    h = make_habit(user, name="Meditate")
    log_habit(h, today, today - D(days=1), today - D(days=2))
    html = auth_client.get(reverse("planner:habits")).content.decode()
    assert "Meditate" in html
    detail = auth_client.get(reverse("planner:habit_detail", args=[h.pk])).content.decode()
    assert "Current streak" in detail and "3 completions recorded" in detail
    assert auth_client.get(reverse("planner:habit_detail", args=[h.pk]), {"month": "2026-02"}).status_code == 200
    assert auth_client.get(reverse("planner:habit_detail", args=[h.pk]), {"month": "nonsense"}).status_code == 200
    assert auth_client.get(reverse("planner:habits"), {"week": "2026-01-01"}).status_code == 200


@pytest.mark.django_db
def test_tracker_empty_state_and_paused_section(auth_client, user, make_habit):
    assert "No habits yet" in auth_client.get(reverse("planner:habits")).content.decode()
    h = make_habit(user, name="Old habit")
    auth_client.post(reverse("planner:habit_pause", args=[h.pk]))
    html = auth_client.get(reverse("planner:habits")).content.decode()
    assert "Paused habits" in html and "Old habit" in html
