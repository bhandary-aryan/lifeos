import datetime as dt
from decimal import Decimal as D

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.services import local_today
from apps.dashboard.reminders import INFO, SOON, URGENT, build_reminders
from apps.finance.models import Debt
from apps.future_plans.models import FuturePlan, PlanAction
from apps.reflections.models import WeeklyReview

pytestmark = pytest.mark.django_db
D1 = dt.timedelta(days=1)
WED = dt.date(2026, 3, 18)            # Wednesday (Monday-start week begins 16 Mar)
FRI = dt.date(2026, 3, 20)


def titles(user, today=WED):
    return [i["title"] for i in build_reminders(user, today)]


def by_title(user, today=WED):
    return {i["title"]: i for i in build_reminders(user, today)}


# ---------------------------------------------------------- preferences
def test_disabled_reminders_return_nothing(user, make_task):
    make_task(user, title="Overdue", due_date=WED - D1)
    user.profile.reminders_enabled = False; user.profile.save()
    assert build_reminders(user, WED) == []


def test_lead_days_controls_the_window(user, make_task):
    make_task(user, title="In 2 days", due_date=WED + 2 * D1)
    make_task(user, title="In 6 days", due_date=WED + 6 * D1)
    make_task(user, title="In 20 days", due_date=WED + 20 * D1)
    user.profile.reminder_lead_days = 1; user.profile.save()
    assert titles(user) == []
    user.profile.reminder_lead_days = 3; user.profile.save()
    assert titles(user) == ["In 2 days"]
    user.profile.reminder_lead_days = 7; user.profile.save()
    assert titles(user) == ["In 2 days", "In 6 days"]
    user.profile.reminder_lead_days = 14; user.profile.save()
    assert "In 20 days" not in titles(user)


# ------------------------------------------------------------------- tasks
def test_task_urgency_and_exclusions(user, make_task):
    make_task(user, title="Overdue", due_date=WED - 3 * D1)
    make_task(user, title="Today", due_date=WED)
    make_task(user, title="Soon", due_date=WED + 2 * D1)
    make_task(user, title="Done", due_date=WED, status="completed")
    make_task(user, title="Cancelled", due_date=WED, status="cancelled")
    make_task(user, title="No date")
    r = by_title(user)
    assert set(r) == {"Overdue", "Today", "Soon"}
    assert r["Overdue"]["level"] == URGENT and "3 days overdue" in r["Overdue"]["detail"]
    assert r["Today"]["level"] == URGENT and "due today" in r["Today"]["detail"]
    assert r["Soon"]["level"] == SOON and "in 2 days" in r["Soon"]["detail"]


def test_relative_wording_singular_and_tomorrow(user, make_task):
    make_task(user, title="Yesterday", due_date=WED - D1); make_task(user, title="Tomorrow", due_date=WED + D1)
    r = by_title(user)
    assert "1 day overdue" in r["Yesterday"]["detail"] and "tomorrow" in r["Tomorrow"]["detail"]


def test_ordering_urgent_then_soon_then_info_then_by_date(user, make_task, make_goal):
    make_task(user, title="Soon B", due_date=WED + 2 * D1); make_task(user, title="Soon A", due_date=WED + D1)
    make_task(user, title="Old", due_date=WED - 5 * D1); make_task(user, title="Now", due_date=WED)
    items = build_reminders(user, FRI)          # Friday: weekly-review nudge becomes active
    assert [i["level"] for i in items] == sorted(i["level"] for i in items)
    assert items[-1]["kind"] == "review" and items[-1]["level"] == INFO


# -------------------------------------------------- milestones, goals, etc.
def test_milestones_and_goal_targets(user, make_goal, make_milestone):
    g = make_goal(user, title="Active goal", target_date=WED + 2 * D1, status="in_progress")
    make_milestone(g, title="Open MS", due_date=WED + D1)
    make_milestone(g, title="Done MS", due_date=WED + D1, status="completed")
    make_milestone(g, title="Far MS", due_date=WED + 30 * D1)
    dead = make_goal(user, title="Abandoned goal", target_date=WED, status="abandoned"); make_milestone(dead, title="Dead MS", due_date=WED)
    fin = make_goal(user, title="Finished goal", target_date=WED, status="completed")
    r = by_title(user)
    assert set(r) == {"Open MS", "Active goal"}
    assert r["Open MS"]["kind"] == "milestone" and "Active goal" in r["Open MS"]["detail"]
    del fin


def test_overdue_goal_and_milestone_are_urgent(user, make_goal, make_milestone):
    g = make_goal(user, title="Late goal", target_date=WED - 2 * D1, status="in_progress")
    make_milestone(g, title="Late MS", due_date=WED - D1)
    assert all(i["level"] == URGENT for i in build_reminders(user, WED))


def test_debt_payment_reminders(user, make_debt):
    make_debt(user, "DueSoon", due_day=20, minimum_payment=D("45"))
    make_debt(user, "DueToday", due_day=18, minimum_payment=D("10"))
    make_debt(user, "NoMinimum", due_day=19, minimum_payment=D("0"))
    make_debt(user, "NoDay", minimum_payment=D("10"))
    make_debt(user, "PaidOff", due_day=19, minimum_payment=D("10"), status=Debt.Status.PAID_OFF)
    make_debt(user, "NextMonth", due_day=5, minimum_payment=D("10"))
    r = by_title(user)
    assert set(r) == {"Payment to DueSoon", "Payment to DueToday"}
    assert r["Payment to DueToday"]["level"] == URGENT and r["Payment to DueSoon"]["level"] == SOON and r["Payment to DueSoon"]["kind"] == "payment"


def test_plan_action_reminders(user):
    plan = FuturePlan.objects.create(owner=user, title="Move house")
    PlanAction.objects.create(owner=user, plan=plan, title="Book removals", due_date=WED + 2 * D1)
    PlanAction.objects.create(owner=user, plan=plan, title="Already done", due_date=WED, is_done=True)
    PlanAction.objects.create(owner=user, plan=plan, title="Later", due_date=WED + 40 * D1)
    PlanAction.objects.create(owner=user, plan=plan, title="Undated")
    r = by_title(user)
    assert set(r) == {"Book removals"} and "Move house" in r["Book removals"]["detail"]


# ------------------------------------------------------------ review nudge
@pytest.mark.parametrize("day,expected", [(dt.date(2026, 3, 16), False), (dt.date(2026, 3, 19), False), (dt.date(2026, 3, 20), True), (dt.date(2026, 3, 22), True)])
def test_review_nudge_appears_late_in_the_week_only(user, day, expected):
    assert ("Weekly review" in titles(user, day)) is expected


def test_review_nudge_hidden_once_reviewed_and_is_informational(user):
    assert by_title(user, FRI)["Weekly review"]["level"] == INFO
    WeeklyReview.objects.create(owner=user, week_start=dt.date(2026, 3, 16), lessons="x")
    assert "Weekly review" not in titles(user, FRI)


def test_review_nudge_follows_sunday_week_start(user):
    user.profile.week_start = 6; user.profile.save()
    sat = dt.date(2026, 3, 21)                               # Sunday-start week began 15 Mar -> day index 6
    assert "Weekly review" in titles(user, sat) and "Weekly review" not in titles(user, dt.date(2026, 3, 18))


# ------------------------------------------------------------- isolation
def test_other_users_items_never_appear(user, other_user, make_task, make_debt):
    make_task(other_user, title="Bob overdue", due_date=WED - D1); make_debt(other_user, "BobBank", due_day=19, minimum_payment=D("5"))
    assert build_reminders(user, WED) == []


def test_result_is_capped(user, make_task):
    for i in range(80):
        make_task(user, title=f"t{i}", due_date=WED)
    assert len(build_reminders(user, WED)) <= 50


# ------------------------------------------------------------------ views
@pytest.fixture
def wednesday(monkeypatch):
    """Pin 'now' to a Wednesday so the Friday-onwards review nudge cannot make these tests depend on the weekday they run."""
    from django.utils import timezone
    monkeypatch.setattr(timezone, "now", lambda: dt.datetime(2026, 3, 18, 12, 0, tzinfo=dt.timezone.utc))


def test_reminders_page_states(auth_client, user, make_task, wednesday):
    assert "all caught up" in auth_client.get(reverse("dashboard:reminders")).content.decode()
    today = local_today(user)
    make_task(user, title="Pay the council", due_date=today - D1)
    h = auth_client.get(reverse("dashboard:reminders")).content.decode()
    assert "Pay the council" in h and "1 day overdue" in h and "Urgent" in h
    user.profile.reminders_enabled = False; user.profile.save()
    assert "Reminders are turned off" in auth_client.get(reverse("dashboard:reminders")).content.decode()


def test_badge_fragment_counts_and_is_accessible(auth_client, user, make_task, wednesday):
    assert "No reminders" in auth_client.get(reverse("dashboard:reminders_badge")).content.decode()
    today = local_today(user)
    make_task(user, title="a", due_date=today); make_task(user, title="b", due_date=today - D1)
    h = auth_client.get(reverse("dashboard:reminders_badge")).content.decode()
    assert ">2<" in h and "2 reminders" in h and 'aria-hidden="true"' in h
    make_task(user, title="c", due_date=today)
    h = auth_client.get(reverse("dashboard:reminders_badge")).content.decode()
    assert "3 reminders" in h


def test_bell_is_in_every_page_and_loads_lazily(auth_client):
    for name in ("dashboard:home", "goals:list", "finance:overview"):
        h = auth_client.get(reverse(name)).content.decode()
        assert reverse("dashboard:reminders_badge") in h and 'aria-label="Reminders"' in h and 'hx-trigger="load"' in h


def test_normal_pages_do_not_compute_reminders(auth_client, user, make_task, django_assert_max_num_queries):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    auth_client.get(reverse("goals:list"))
    for i in range(30):
        make_task(user, title=f"t{i}", due_date=local_today(user))
    with CaptureQueriesContext(connection) as q:
        auth_client.get(reverse("goals:list"))
    assert not any("planner_task" in x["sql"] for x in q.captured_queries)               # the bell fetches its count separately


def test_dashboard_weekly_review_card(auth_client, user):
    h = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Optional: a few minutes to look back" in h and "Start review" in h
    today = local_today(user); ws = today - dt.timedelta(days=today.weekday())
    WeeklyReview.objects.create(owner=user, week_start=ws, lessons="x")
    h = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "This week is reviewed" in h and "Open review" in h


def test_anonymous_reminder_endpoints_redirect(client):
    for name in ("dashboard:reminders", "dashboard:reminders_badge"):
        assert client.get(reverse(name)).status_code == 302
