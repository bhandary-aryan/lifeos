import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.services import local_today
from apps.planner.models import Task
from apps.planner.services import day_load, week_plan, week_start_for

pytestmark = pytest.mark.django_db
D = dt.timedelta


def task_data(**over):
    d = {"title": "Write report", "priority": "medium", "status": "todo", "weight": "1"}
    d.update(over)
    return d


# ---------- CRUD ----------
def test_create_edit_delete_task(auth_client, user):
    today = local_today(user)
    auth_client.post(reverse("planner:task_create"), task_data(due_date=today.isoformat(), estimated_minutes="45"))
    t = Task.objects.get(owner=user)
    assert (t.title, t.estimated_minutes, t.due_date) == ("Write report", 45, today)
    auth_client.post(reverse("planner:task_update", args=[t.pk]), task_data(title="Write final report", priority="high"))
    t.refresh_from_db(); assert (t.title, t.priority) == ("Write final report", "high")
    assert auth_client.get(reverse("planner:task_delete", args=[t.pk])).status_code == 200
    assert Task.objects.filter(pk=t.pk).exists()
    auth_client.post(reverse("planner:task_delete", args=[t.pk]))
    assert not Task.objects.exists()


def test_task_form_validation_errors(auth_client):
    r = auth_client.post(reverse("planner:task_create"), task_data(title="", estimated_minutes="99999", weight="0"))
    assert r.status_code == 200 and not Task.objects.exists()
    assert "required" in r.content.decode().lower()


def test_toggle_completes_and_reopens_with_timestamp(auth_client, user, make_task):
    t = make_task(user)
    auth_client.post(reverse("planner:task_toggle", args=[t.pk]))
    t.refresh_from_db(); assert t.status == "completed" and t.completed_at
    auth_client.post(reverse("planner:task_toggle", args=[t.pk]))
    t.refresh_from_db(); assert t.status == "todo" and t.completed_at is None


def test_toggle_requires_post(auth_client, user, make_task):
    assert auth_client.get(reverse("planner:task_toggle", args=[make_task(user).pk])).status_code == 405


@pytest.mark.parametrize("evil", ["https://evil.example/phish", "//evil.example", "javascript:alert(1)"])
def test_next_redirect_rejects_external_urls(auth_client, user, make_task, evil):
    r = auth_client.post(reverse("planner:task_toggle", args=[make_task(user).pk]), {"next": evil})
    assert r.url == reverse("planner:today")


def test_quick_add_creates_task_and_reports_errors(auth_client, user):
    today = local_today(user)
    auth_client.post(reverse("planner:quick_add"), {"title": "Call bank", "due_date": today.isoformat(), "priority": "high"})
    assert Task.objects.get(owner=user).title == "Call bank"
    r = auth_client.post(reverse("planner:quick_add"), {"title": "", "priority": "high"}, follow=True)
    assert Task.objects.count() == 1 and "Task not added" in r.content.decode()


# ---------- views: today / upcoming / overdue / all ----------
def test_view_classification(auth_client, user, make_task):
    today = local_today(user)
    make_task(user, title="TodayT", due_date=today)
    make_task(user, title="SoonT", due_date=today + D(days=3))
    make_task(user, title="LateT", due_date=today - D(days=2))
    make_task(user, title="LateDone", due_date=today - D(days=2), status="completed")
    make_task(user, title="Undated")
    def page(name):
        h = auth_client.get(reverse(name)).content.decode()
        return {t for t in ("TodayT", "SoonT", "LateT", "LateDone", "Undated") if t in h}
    assert page("planner:upcoming") == {"SoonT"}
    assert page("planner:overdue") == {"LateT"}                     # completed tasks are never overdue
    assert page("planner:all") == {"TodayT", "SoonT", "LateT", "LateDone", "Undated"}
    today_page = page("planner:today")
    assert "TodayT" in today_page and "LateT" in today_page         # overdue shown on today as attention items
    assert "SoonT" not in today_page


def test_cancelled_tasks_not_overdue_or_planned(auth_client, user, make_task):
    today = local_today(user)
    make_task(user, title="Dropped", due_date=today - D(days=1), status="cancelled")
    assert "Dropped" not in auth_client.get(reverse("planner:overdue")).content.decode()
    assert "Dropped" not in auth_client.get(reverse("planner:today")).content.decode()


def test_empty_states(auth_client):
    assert "No tasks yet" in auth_client.get(reverse("planner:all")).content.decode()
    assert "Nothing overdue" in auth_client.get(reverse("planner:overdue")).content.decode()
    assert "Nothing scheduled" in auth_client.get(reverse("planner:today")).content.decode()


def test_search_filter_sort(auth_client, user, make_task):
    make_task(user, title="Alpha email", priority="low")
    make_task(user, title="Bravo email", priority="high")
    make_task(user, title="Charlie call", priority="medium", status="in_progress")
    url = reverse("planner:all")
    h = auth_client.get(url, {"q": "email"}).content.decode()
    assert "Alpha" in h and "Bravo" in h and "Charlie" not in h
    h = auth_client.get(url, {"status": "in_progress"}).content.decode()
    assert "Charlie" in h and "Alpha" not in h
    h = auth_client.get(url, {"priority": "high"}).content.decode()
    assert "Bravo" in h and "Alpha" not in h
    h = auth_client.get(url, {"sort": "priority"}).content.decode()
    assert h.index("Bravo") < h.index("Charlie") < h.index("Alpha")
    h = auth_client.get(url, {"sort": "title"}).content.decode()
    assert h.index("Alpha") < h.index("Bravo") < h.index("Charlie")


def test_pagination(auth_client, user, make_task):
    for i in range(27):
        make_task(user, title=f"T{i:02d}")
    assert "Page 1 of 2" in auth_client.get(reverse("planner:all")).content.decode()
    assert auth_client.get(reverse("planner:all"), {"page": 2}).status_code == 200


# ---------- capacity ----------
def test_day_load_counts_only_open_estimates(user, make_task):
    d = local_today(user)
    make_task(user, due_date=d, estimated_minutes=60)
    make_task(user, due_date=d, estimated_minutes=30, status="in_progress")
    make_task(user, due_date=d, estimated_minutes=500, status="completed")
    make_task(user, due_date=d, estimated_minutes=500, status="cancelled")
    make_task(user, due_date=d)
    assert day_load(user, d) == (90, 1)


def test_day_view_warns_when_over_capacity_and_changes_nothing(auth_client, user, make_task):
    user.profile.daily_available_minutes = 240; user.profile.save()
    d = local_today(user)
    a = make_task(user, due_date=d, estimated_minutes=200); b = make_task(user, due_date=d, estimated_minutes=100)
    html = auth_client.get(reverse("planner:today")).content.decode()
    assert "exceeds your available time by 60 minutes" in html and "nothing is changed automatically" in html
    a.refresh_from_db(); b.refresh_from_db()
    assert a.due_date == d and b.due_date == d


def test_day_view_no_warning_within_capacity(auth_client, user, make_task):
    make_task(user, due_date=local_today(user), estimated_minutes=60)
    assert "exceeds your available time" not in auth_client.get(reverse("planner:today")).content.decode()


def test_week_plan_buckets_and_flags_over_capacity(user, make_task):
    start = week_start_for(local_today(user), 0)
    make_task(user, due_date=start, estimated_minutes=300)
    make_task(user, due_date=start, estimated_minutes=300)
    make_task(user, due_date=start + D(days=2), estimated_minutes=120)
    make_task(user, due_date=start + D(days=7), estimated_minutes=999)   # next week: excluded
    plan = week_plan(user, start, 480)
    assert [len(p["tasks"]) for p in plan] == [2, 0, 1, 0, 0, 0, 0]
    assert plan[0]["planned_minutes"] == 600 and plan[0]["over_by"] == 120 and plan[0]["load_pct"] == 100
    assert plan[2]["over_by"] == 0 and plan[2]["load_pct"] == 25


def test_week_plan_zero_availability_does_not_divide_by_zero(user, make_task):
    start = week_start_for(local_today(user), 0)
    make_task(user, due_date=start, estimated_minutes=30)
    assert week_plan(user, start, 0)[0]["load_pct"] == 0


def test_week_view_renders_and_marks_overload(auth_client, user, make_task):
    start = week_start_for(local_today(user), user.profile.week_start)
    make_task(user, title="Big job", due_date=start, estimated_minutes=900)
    html = auth_client.get(reverse("planner:week")).content.decode()
    assert "Big job" in html and "over by 420" in html and "nothing is rescheduled automatically" in html
    assert auth_client.get(reverse("planner:week"), {"start": "2026-01-01"}).status_code == 200
    assert auth_client.get(reverse("planner:week"), {"start": "garbage"}).status_code == 200


def test_week_start_respects_user_setting():
    wed = dt.date(2026, 3, 4)
    assert week_start_for(wed, 0) == dt.date(2026, 3, 2)      # Monday
    assert week_start_for(wed, 6) == dt.date(2026, 3, 1)      # Sunday
    assert week_start_for(wed, 5) == dt.date(2026, 2, 28)     # Saturday


# ---------- timezones ----------
def test_today_follows_the_users_timezone(auth_client, user, make_task, monkeypatch):
    instant = dt.datetime(2026, 3, 1, 23, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(timezone, "now", lambda: instant)
    user.profile.timezone = "Pacific/Auckland"; user.profile.save()     # already 2 March there
    make_task(user, title="NZ today", due_date=dt.date(2026, 3, 2))
    make_task(user, title="NZ yesterday", due_date=dt.date(2026, 3, 1))
    html = auth_client.get(reverse("planner:today")).content.decode()
    assert "Sunday, 1 March" not in html and "Monday, 2 March 2026" in html
    assert "NZ today" in html
    assert "NZ yesterday" in auth_client.get(reverse("planner:overdue")).content.decode()


def test_time_block_entered_in_user_timezone_is_stored_utc(auth_client, user):
    user.profile.timezone = "Asia/Tokyo"; user.profile.save()
    auth_client.post(reverse("planner:task_create"), task_data(start_at="2026-06-01T09:00"))
    t = Task.objects.get(owner=user)
    assert t.start_at == dt.datetime(2026, 6, 1, 0, 0, tzinfo=dt.timezone.utc)   # 09:00 JST


def test_quick_add_dropdown_links_are_live(auth_client):
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    for name in ("planner:task_create", "goals:create", "planner:habit_create"):
        assert f'href="{reverse(name)}"' in html
