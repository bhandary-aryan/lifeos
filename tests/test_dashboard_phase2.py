import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.services import local_today
from apps.dashboard.services import build_dashboard_summary
from apps.goals.services import goal_progress

pytestmark = pytest.mark.django_db
D = dt.timedelta


def test_summary_sections_match_underlying_records(user, make_task, make_goal, make_habit, log_habit):
    today = local_today(user)
    make_task(user, title="due today A", due_date=today, priority="low")
    make_task(user, title="due today B", due_date=today, priority="high")
    make_task(user, title="done today", due_date=today, status="completed")
    make_task(user, title="late 1", due_date=today - D(days=1))
    make_task(user, title="late 2", due_date=today - D(days=9))
    make_task(user, title="soon", due_date=today + D(days=5))
    make_task(user, title="far", due_date=today + D(days=30))
    make_task(user, title="cancelled today", due_date=today, status="cancelled")
    s = build_dashboard_summary(user)
    assert [t.title for t in s["priorities"]] == ["due today B", "due today A"]       # high before low
    assert s["overdue_count"] == 2 and {t.title for t in s["overdue"]} == {"late 1", "late 2"}
    assert [t.title for t in s["due_soon"]] == ["soon"]
    assert (s["tasks_done_today"], s["tasks_due_today_total"]) == (1, 3)
    assert s["has_any_data"] is True


def test_dashboard_goal_progress_equals_goal_page_progress(auth_client, user, make_goal, make_milestone, make_task):
    g = make_goal(user, title="Tracked", progress_mode="task", status="in_progress")
    for status in ("completed", "completed", "todo"):
        make_task(user, goal=g, status=status)
    s = build_dashboard_summary(user)
    assert s["goals"][0].progress == goal_progress(g) == 67
    assert 'value="67"' in auth_client.get(reverse("dashboard:home")).content.decode()


def test_completed_and_abandoned_goals_excluded_from_active(user, make_goal):
    make_goal(user, title="Active"); make_goal(user, title="Done", status="completed"); make_goal(user, title="Dropped", status="abandoned")
    assert [g.title for g in build_dashboard_summary(user)["goals"]] == ["Active"]


def test_only_five_goals_shown(user, make_goal):
    for i in range(8):
        make_goal(user, title=f"G{i}")
    assert len(build_dashboard_summary(user)["goals"]) == 5


def test_habits_today_respects_schedule_and_state(user, make_habit, log_habit):
    today = local_today(user)
    daily = make_habit(user, name="Daily")
    off_day = make_habit(user, name="OffDay", frequency="weekdays", weekdays=[(today.weekday() + 1) % 7])
    on_day = make_habit(user, name="OnDay", frequency="weekdays", weekdays=[today.weekday()])
    weekly = make_habit(user, name="Weekly", frequency="weekly", weekly_target=3)
    paused = make_habit(user, name="Paused", is_active=False, paused_on=today)
    future = make_habit(user, name="Future", start_date=today + D(days=3))
    log_habit(daily, today, today - D(days=1))
    s = build_dashboard_summary(user)
    names = {r["habit"].name: r for r in s["habits"]}
    assert set(names) == {"Daily", "OnDay", "Weekly"}
    assert names["Daily"]["done_today"] and names["Daily"]["streak"] == 2
    assert not names["OnDay"]["done_today"]
    assert s["habits_done"] == 1


def test_dashboard_isolated_per_user(user, other_user, make_task, make_goal, make_habit):
    make_task(other_user, title="Bob task", due_date=local_today(other_user))
    make_goal(other_user, title="Bob goal"); make_habit(other_user, name="Bob habit")
    s = build_dashboard_summary(user)
    assert not s["priorities"] and not s["goals"] and not s["habits"] and s["has_any_data"] is False


def test_empty_state_vs_populated(auth_client, user, make_task):
    assert "Nothing here yet" in auth_client.get(reverse("dashboard:home")).content.decode()
    make_task(user, title="Visible", due_date=local_today(user))
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Nothing here yet" not in html and "Visible" in html


def test_complete_task_directly_from_dashboard(auth_client, user, make_task):
    t = make_task(user, title="Quick win", due_date=local_today(user))
    home = reverse("dashboard:home")
    html = auth_client.get(home).content.decode()
    assert reverse("planner:task_toggle", args=[t.pk]) in html and "0 of 1 done" in html
    r = auth_client.post(reverse("planner:task_toggle", args=[t.pk]), {"next": home})
    assert r.url == home
    assert "1 of 1 done" in auth_client.get(home).content.decode()


def test_log_habit_directly_from_dashboard(auth_client, user, make_habit):
    h = make_habit(user, name="Stretch")
    home = reverse("dashboard:home")
    auth_client.post(reverse("planner:habit_log", args=[h.pk]), {"date": local_today(user).isoformat(), "next": home})
    assert "1 of 1 done" in auth_client.get(home).content.decode()


def test_overdue_banner_appears_only_when_needed(auth_client, user, make_task):
    assert "overdue task" not in auth_client.get(reverse("dashboard:home")).content.decode()
    make_task(user, due_date=local_today(user) - D(days=1))
    assert "1 overdue task still needs attention" in auth_client.get(reverse("dashboard:home")).content.decode()


def test_dashboard_query_count_is_bounded_and_flat(auth_client, user, make_task, make_goal, make_habit, make_milestone):
    home = reverse("dashboard:home")
    auth_client.get(home)
    with CaptureQueriesContext(connection) as small:
        auth_client.get(home)
    today = local_today(user)
    for i in range(10):
        make_task(user, title=f"t{i}", due_date=today + D(days=i % 3))
        g = make_goal(user, title=f"g{i}", progress_mode="task"); make_task(user, goal=g)
        make_habit(user, name=f"h{i}")
    with CaptureQueriesContext(connection) as large:
        auth_client.get(home)
    assert len(large) == len(small) + 0 or len(large) <= len(small) + 4     # data present adds a few fixed queries, never per-row
    assert len(large) < 30


def test_finance_prompt_shown_until_an_account_exists(auth_client, make_account, user):
    assert "Add an account to start tracking" in auth_client.get(reverse("dashboard:home")).content.decode()
    make_account(user)
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Add an account to start tracking" not in html and "Finances this month" in html
