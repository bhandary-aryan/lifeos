"""Alice's records must be unreachable for Bob by guessing IDs, on every Phase 2 endpoint."""
import datetime as dt

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.services import local_today
from apps.goals.models import Goal, LifeArea, Milestone
from apps.planner.models import Habit, HabitLog, Task

pytestmark = pytest.mark.django_db


@pytest.fixture
def alice_data(user, make_goal, make_milestone, make_task, make_habit, log_habit):
    g = make_goal(user, title="Alice goal", progress_mode="task")
    m = make_milestone(g, title="Alice milestone")
    t = make_task(user, title="Alice task", goal=g, milestone=m)
    h = make_habit(user, name="Alice habit")
    log_habit(h, local_today(user))
    area = LifeArea.objects.filter(owner=user).first()
    return {"goal": g, "ms": m, "task": t, "habit": h, "area": area}


@pytest.fixture
def bob(other_user):
    c = Client()
    c.force_login(other_user)
    return c


def urls(d):
    g, m, t, h, a = d["goal"].pk, d["ms"].pk, d["task"].pk, d["habit"].pk, d["area"].pk
    return {
        "get": [
            reverse("goals:detail", args=[g]), reverse("goals:update", args=[g]), reverse("goals:delete", args=[g]),
            reverse("goals:milestone_update", args=[m]), reverse("goals:milestone_delete", args=[m]),
            reverse("planner:task_update", args=[t]), reverse("planner:task_delete", args=[t]),
            reverse("planner:habit_detail", args=[h]), reverse("planner:habit_update", args=[h]),
            reverse("planner:habit_delete", args=[h]),
        ],
        "post": [
            reverse("goals:update", args=[g]), reverse("goals:delete", args=[g]),
            reverse("goals:milestone_add", args=[g]), reverse("goals:milestone_update", args=[m]),
            reverse("goals:milestone_delete", args=[m]), reverse("goals:milestone_toggle", args=[m]),
            reverse("goals:area_delete", args=[a]),
            reverse("planner:task_update", args=[t]), reverse("planner:task_delete", args=[t]),
            reverse("planner:task_toggle", args=[t]),
            reverse("planner:habit_update", args=[h]), reverse("planner:habit_delete", args=[h]),
            reverse("planner:habit_log", args=[h]), reverse("planner:habit_pause", args=[h]),
        ],
    }


def snapshot():
    return (
        list(Goal.objects.values_list("pk", "title", "status", "manual_progress")),
        list(Milestone.objects.values_list("pk", "title", "status")),
        list(Task.objects.values_list("pk", "title", "status")),
        list(Habit.objects.values_list("pk", "name", "is_active")),
        list(HabitLog.objects.values_list("pk", "date")),
        LifeArea.objects.count(),
    )


def test_other_user_gets_404_on_every_get_and_post_and_nothing_changes(bob, alice_data):
    before = snapshot()
    u = urls(alice_data)
    for url in u["get"]:
        assert bob.get(url).status_code == 404, f"GET {url}"
    for url in u["post"]:
        r = bob.post(url, {"title": "HACKED", "name": "HACKED", "status": "completed", "date": local_today(alice_data["goal"].owner).isoformat()})
        assert r.status_code == 404, f"POST {url} -> {r.status_code}"
    assert snapshot() == before


def test_anonymous_is_redirected_to_login_everywhere(client, alice_data):
    before = snapshot()
    u = urls(alice_data)
    for url in u["get"]:
        r = client.get(url)
        assert r.status_code == 302 and r.url.startswith(reverse("accounts:login")), url
    for url in u["post"]:
        r = client.post(url, {"title": "x"})
        assert r.status_code == 302 and r.url.startswith(reverse("accounts:login")), url
    assert snapshot() == before


def test_404_pages_do_not_leak_other_users_titles(bob, alice_data):
    for url in urls(alice_data)["get"]:
        body = bob.get(url).content.decode()
        assert "Alice" not in body


def test_lists_only_show_own_records(bob, alice_data):
    for name in ("goals:list", "planner:all", "planner:today", "planner:upcoming", "planner:overdue", "planner:habits", "planner:week", "dashboard:home"):
        assert "Alice" not in bob.get(reverse(name)).content.decode(), name


def test_list_filters_cannot_reach_other_users_data_by_id(bob, alice_data, other_user):
    g = alice_data["goal"]
    html = bob.get(reverse("planner:all"), {"goal": g.pk}).content.decode()
    assert "Alice" not in html
    html = bob.get(reverse("goals:list"), {"area": alice_data["area"].pk}).content.decode()
    assert "Alice" not in html


def test_quick_add_cannot_attach_task_to_another_users_goal_or_milestone(bob, other_user, alice_data):
    r = bob.post(reverse("planner:quick_add"), {
        "title": "Sneaky", "priority": "low", "goal": alice_data["goal"].pk}, follow=True)
    assert not Task.objects.filter(title="Sneaky").exists() and "Task not added" in r.content.decode()
    bob.post(reverse("planner:quick_add"), {"title": "Sneaky2", "priority": "low", "milestone": alice_data["ms"].pk})
    assert not Task.objects.filter(title="Sneaky2").exists()


def test_task_create_cannot_reference_foreign_goal(bob, alice_data):
    r = bob.post(reverse("planner:task_create"), {"title": "X", "priority": "low", "status": "todo", "weight": 1, "goal": alice_data["goal"].pk})
    assert r.status_code == 200 and not Task.objects.filter(title="X").exists()


def test_goal_create_cannot_reference_foreign_life_area(bob, alice_data):
    r = bob.post(reverse("goals:create"), {"title": "X", "priority": "low", "status": "not_started", "progress_mode": "manual", "manual_progress": 0, "life_area": alice_data["area"].pk})
    assert r.status_code == 200 and not Goal.objects.filter(title="X").exists()


def test_habit_create_cannot_use_foreign_life_area(bob, alice_data):
    r = bob.post(reverse("planner:habit_create"), {"name": "X", "frequency": "daily", "start_date": "2026-03-01", "category": alice_data["area"].pk})
    assert r.status_code == 200 and not Habit.objects.filter(name="X").exists()


def test_model_level_ownership_validation(other_user, alice_data):
    from django.core.exceptions import ValidationError
    bad_ms = Milestone(owner=other_user, goal=alice_data["goal"], title="x")
    with pytest.raises(ValidationError):
        bad_ms.full_clean()
    bad_task = Task(owner=other_user, title="x", goal=alice_data["goal"])
    with pytest.raises(ValidationError):
        bad_task.full_clean()
    from apps.planner.models import HabitLog as HL
    with pytest.raises(ValidationError):
        HL(owner=other_user, habit=alice_data["habit"], date=dt.date(2026, 1, 1)).full_clean()


def test_other_users_progress_and_stats_not_affected(bob, user, other_user, alice_data):
    Task.objects.filter(pk=alice_data["task"].pk).update(status="completed")
    from apps.goals.services import goal_progress
    assert goal_progress(alice_data["goal"]) == 100
    html = bob.get(reverse("dashboard:home")).content.decode()
    assert "Alice" not in html and "100%" not in html


def test_state_changing_endpoints_enforce_csrf(user, alice_data):
    c = Client(enforce_csrf_checks=True)
    c.force_login(user)
    for url in urls(alice_data)["post"][:6] + [reverse("planner:task_toggle", args=[alice_data["task"].pk])]:
        assert c.post(url, {}).status_code == 403, url
