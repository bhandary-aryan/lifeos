import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.goals.forms import GoalForm, LifeAreaForm, MilestoneForm
from apps.goals.models import Goal, LifeArea, Milestone
from apps.goals.services import goal_progress, percent
from apps.planner.forms import TaskForm
from apps.planner.models import Task
from apps.core.services import local_today
import datetime as dt

pytestmark = pytest.mark.django_db


# ---------- percent helper ----------
@pytest.mark.parametrize("done,total,expected", [(0, 0, 0), (0, 5, 0), (1, 8, 13), (1, 3, 33), (2, 3, 67), (5, 5, 100), (7, 5, 100), (None, None, 0)])
def test_percent_rounding_and_zero_division(done, total, expected):
    assert percent(done, total) == expected


# ---------- progress modes ----------
def test_manual_mode_zero_mid_and_complete(user, make_goal):
    for value in (0, 37, 100):
        g = make_goal(user, progress_mode="manual", manual_progress=value)
        assert goal_progress(g) == value


def test_manual_mode_ignores_milestones_and_tasks(user, make_goal, make_milestone, make_task):
    g = make_goal(user, progress_mode="manual", manual_progress=10)
    make_milestone(g, status="completed")
    make_task(user, goal=g, status="completed")
    assert goal_progress(g) == 10


def test_milestone_mode_no_milestones_is_zero(user, make_goal):
    assert goal_progress(make_goal(user, progress_mode="milestone")) == 0


def test_milestone_mode_uses_weights(user, make_goal, make_milestone):
    g = make_goal(user, progress_mode="milestone")
    make_milestone(g, weight=1, status="completed")
    big = make_milestone(g, weight=3)
    assert goal_progress(g) == 25
    big.status = "completed"; big.save()
    assert goal_progress(g) == 100


def test_milestone_mode_partial_status_does_not_count(user, make_goal, make_milestone):
    g = make_goal(user, progress_mode="milestone")
    make_milestone(g, status="in_progress")
    make_milestone(g, status="completed")
    assert goal_progress(g) == 50


def test_task_mode_no_tasks_is_zero(user, make_goal):
    assert goal_progress(make_goal(user, progress_mode="task")) == 0


def test_task_mode_weighted_and_cancelled_ignored(user, make_goal, make_task):
    g = make_goal(user, progress_mode="task")
    make_task(user, goal=g, weight=1, status="completed")
    make_task(user, goal=g, weight=3, status="todo")
    make_task(user, goal=g, weight=10, status="cancelled")
    assert goal_progress(g) == 25


def test_task_linked_to_goal_and_milestone_counted_once(user, make_goal, make_milestone, make_task):
    g = make_goal(user, progress_mode="task")
    m = make_milestone(g)
    make_task(user, goal=g, milestone=m, status="completed")
    make_task(user, goal=g, status="todo")
    assert goal_progress(g) == 50          # 1 of 2, not 2 of 3


def test_milestone_only_task_is_attached_to_goal_automatically(user, make_goal, make_milestone, make_task):
    g = make_goal(user, progress_mode="task")
    m = make_milestone(g)
    t = make_task(user, milestone=m, status="completed")
    assert t.goal_id == g.pk and goal_progress(g) == 100


def test_task_completion_changes_goal_progress(auth_client, user, make_goal, make_task):
    g = make_goal(user, progress_mode="task")
    t1 = make_task(user, goal=g); make_task(user, goal=g)
    assert goal_progress(g) == 0
    auth_client.post(reverse("planner:task_toggle", args=[t1.pk]))
    assert goal_progress(g) == 50
    auth_client.post(reverse("planner:task_toggle", args=[t1.pk]))
    assert goal_progress(g) == 0


def test_mixed_milestones_and_tasks_do_not_multiply_each_other(user, make_goal, make_milestone, make_task):
    """Joining two child tables naively would inflate sums; subqueries must not."""
    g = make_goal(user, progress_mode="milestone")
    make_milestone(g, weight=1, status="completed"); make_milestone(g, weight=1); make_milestone(g, weight=2)
    for _ in range(4):
        make_task(user, goal=g)
    assert goal_progress(g) == 25
    g.progress_mode = "task"; g.save()
    assert goal_progress(g) == 0


def test_completed_status_sets_and_clears_completed_at(user, make_goal):
    g = make_goal(user)
    assert g.completed_at is None
    g.status = "completed"; g.save()
    assert g.completed_at is not None
    g.status = "in_progress"; g.save()
    assert g.completed_at is None


def test_milestone_and_task_completion_timestamps(user, make_goal, make_milestone, make_task):
    m = make_milestone(make_goal(user), status="completed")
    t = make_task(user, status="completed")
    assert m.completed_at and t.completed_at
    m.status = "todo"; m.save(); t.status = "todo"; t.save()
    assert m.completed_at is None and t.completed_at is None


# ---------- validation / constraints ----------
def goal_data(**over):
    d = {"title": "Learn Rust", "description": "", "priority": "medium", "status": "not_started",
         "progress_mode": "manual", "manual_progress": "10"}
    d.update(over)
    return d


@pytest.mark.parametrize("value", ["-1", "101", "abc"])
def test_goal_form_rejects_bad_manual_progress(user, value):
    assert not GoalForm(goal_data(manual_progress=value), user=user).is_valid()


def test_goal_form_rejects_target_before_start(user):
    form = GoalForm(goal_data(start_date="2026-05-10", target_date="2026-05-01"), user=user)
    assert not form.is_valid() and "target_date" in form.errors


def test_db_check_constraints_backstop(user, make_goal, make_milestone):
    g = make_goal(user)
    with pytest.raises(IntegrityError), transaction.atomic():
        Goal.objects.filter(pk=g.pk).update(manual_progress=150)
    with pytest.raises(IntegrityError), transaction.atomic():
        Goal.objects.filter(pk=g.pk).update(start_date=dt.date(2026, 5, 10), target_date=dt.date(2026, 5, 1))
    m = make_milestone(g)
    with pytest.raises(IntegrityError), transaction.atomic():
        Milestone.objects.filter(pk=m.pk).update(weight=0)


def test_milestone_form_rejects_zero_weight(user, make_goal):
    g = make_goal(user)
    assert not MilestoneForm({"title": "x", "status": "todo", "order": 0, "weight": 0}, user=user, goal=g).is_valid()


def test_goal_form_cannot_use_another_users_life_area(user, other_user):
    foreign = LifeArea.objects.get(owner=other_user, name="Career")
    form = GoalForm(goal_data(life_area=foreign.pk), user=user)
    assert not form.is_valid() and "life_area" in form.errors


def test_task_form_cannot_link_foreign_goal_or_milestone(user, other_user, make_goal, make_milestone):
    g = make_goal(other_user); m = make_milestone(g)
    assert not TaskForm({"title": "t", "priority": "low", "status": "todo", "weight": 1, "goal": g.pk}, user=user).is_valid()
    assert not TaskForm({"title": "t", "priority": "low", "status": "todo", "weight": 1, "milestone": m.pk}, user=user).is_valid()


def test_task_milestone_must_belong_to_chosen_goal(user, make_goal, make_milestone):
    g1, g2 = make_goal(user, title="A"), make_goal(user, title="B")
    m = make_milestone(g2)
    form = TaskForm({"title": "t", "priority": "low", "status": "todo", "weight": 1, "goal": g1.pk, "milestone": m.pk}, user=user)
    assert not form.is_valid() and "milestone" in form.errors


# ---------- life areas ----------
def test_default_life_areas_seeded_for_new_users(user):
    names = set(LifeArea.objects.filter(owner=user).values_list("name", flat=True))
    assert {"Career", "Education", "Financial Stability", "Health", "Relationships", "Home", "Personal Growth", "Creativity", "Travel"} <= names


def test_life_areas_are_per_user(user, other_user):
    LifeArea.objects.create(owner=user, name="Volunteering")
    assert not LifeArea.objects.filter(owner=other_user, name="Volunteering").exists()


def test_life_area_unique_case_insensitive(user):
    assert not LifeAreaForm({"name": "career"}, user=user).is_valid()
    with pytest.raises(IntegrityError), transaction.atomic():
        LifeArea.objects.create(owner=user, name="CAREER")


def test_add_and_delete_custom_life_area_keeps_goals(auth_client, user, make_goal):
    auth_client.post(reverse("goals:areas"), {"name": "Volunteering"})
    area = LifeArea.objects.get(owner=user, name="Volunteering")
    g = make_goal(user, life_area=area)
    auth_client.post(reverse("goals:area_delete", args=[area.pk]))
    g.refresh_from_db()
    assert g.life_area is None and Goal.objects.filter(pk=g.pk).exists()


# ---------- CRUD views ----------
def test_create_edit_delete_goal_flow(auth_client, user):
    r = auth_client.post(reverse("goals:create"), goal_data())
    g = Goal.objects.get(owner=user)
    assert r.status_code == 302 and r.url == reverse("goals:detail", args=[g.pk])
    auth_client.post(reverse("goals:update", args=[g.pk]), goal_data(title="Learn Go", manual_progress="40"))
    g.refresh_from_db()
    assert (g.title, g.manual_progress) == ("Learn Go", 40)
    assert auth_client.get(reverse("goals:delete", args=[g.pk])).status_code == 200   # confirmation page
    assert Goal.objects.filter(pk=g.pk).exists()
    auth_client.post(reverse("goals:delete", args=[g.pk]))
    assert not Goal.objects.filter(pk=g.pk).exists()


def test_invalid_goal_form_shows_errors_and_saves_nothing(auth_client, user):
    r = auth_client.post(reverse("goals:create"), goal_data(manual_progress="500", title=""))
    assert r.status_code == 200 and not Goal.objects.exists()
    html = r.content.decode()
    assert "This field is required" in html


def test_deleting_goal_removes_milestones_but_keeps_tasks(auth_client, user, make_goal, make_milestone, make_task):
    g = make_goal(user); m = make_milestone(g); t = make_task(user, goal=g, milestone=m)
    auth_client.post(reverse("goals:delete", args=[g.pk]))
    assert not Milestone.objects.filter(pk=m.pk).exists()
    t.refresh_from_db()
    assert t.goal is None and t.milestone is None


def test_goal_detail_shows_progress_milestones_tasks_and_deadlines(auth_client, user, make_goal, make_milestone, make_task):
    today = local_today(user)
    g = make_goal(user, title="Ship app", progress_mode="milestone")
    m1 = make_milestone(g, title="Design", status="completed")
    m2 = make_milestone(g, title="Build", due_date=today + dt.timedelta(days=3))
    make_task(user, title="Write schema", goal=g, milestone=m2)
    make_task(user, title="Loose task", goal=g, due_date=today + dt.timedelta(days=2))
    html = auth_client.get(reverse("goals:detail", args=[g.pk])).content.decode()
    for text in ("Ship app", "Design", "Build", "Write schema", "Loose task", "1 of 2 milestone", 'value="50"', "Upcoming deadlines"):
        assert text in html


def test_complete_milestone_and_task_from_goal_page(auth_client, user, make_goal, make_milestone, make_task):
    g = make_goal(user, progress_mode="milestone"); m = make_milestone(g); t = make_task(user, goal=g, milestone=m)
    detail = reverse("goals:detail", args=[g.pk])
    r = auth_client.post(reverse("goals:milestone_toggle", args=[m.pk]), {"next": detail})
    assert r.url == detail
    m.refresh_from_db(); assert m.status == "completed"
    r = auth_client.post(reverse("planner:task_toggle", args=[t.pk]), {"next": detail})
    assert r.url == detail
    t.refresh_from_db(); assert t.status == "completed"
    assert 'value="100"' in auth_client.get(detail).content.decode()


def test_add_milestone_from_goal_page(auth_client, user, make_goal):
    g = make_goal(user)
    auth_client.post(reverse("goals:milestone_add", args=[g.pk]), {"title": "First", "weight": "2", "status": "todo", "order": "0"})
    assert g.milestones.get().weight == 2
    r = auth_client.post(reverse("goals:milestone_add", args=[g.pk]), {"title": "Bad", "weight": "0", "status": "todo", "order": "0"}, follow=True)
    assert g.milestones.count() == 1 and "not saved" in r.content.decode()


# ---------- list: filters, sort, pagination, layouts, empty ----------
def test_goal_list_empty_state(auth_client):
    assert "No goals yet" in auth_client.get(reverse("goals:list")).content.decode()


def test_goal_list_filters(auth_client, user, make_goal):
    today = local_today(user)
    area = LifeArea.objects.get(owner=user, name="Health")
    make_goal(user, title="Run a 10k", life_area=area, priority="high", status="in_progress", target_date=today - dt.timedelta(days=5))
    make_goal(user, title="Read books", priority="low", target_date=today + dt.timedelta(days=10))
    make_goal(user, title="Old done", status="completed", target_date=today - dt.timedelta(days=50))
    url = reverse("goals:list")
    def titles(**q):
        html = auth_client.get(url, q).content.decode()
        return {t for t in ("Run a 10k", "Read books", "Old done") if t in html}
    assert titles() == {"Run a 10k", "Read books", "Old done"}
    assert titles(area=area.pk) == {"Run a 10k"}
    assert titles(priority="low") == {"Read books"}
    assert titles(status="completed") == {"Old done"}
    assert titles(due="overdue") == {"Run a 10k"}        # completed goals are not "overdue"
    assert titles(due="30") == {"Read books"}
    assert titles(q="10k") == {"Run a 10k"}
    assert titles(priority="bogus") == {"Run a 10k", "Read books", "Old done"}   # invalid filter ignored


def test_goal_list_sort_by_priority_puts_high_first(auth_client, user, make_goal):
    for title, pr in (("Lowgoal", "low"), ("Highgoal", "high"), ("Medgoal", "medium")):
        make_goal(user, title=title, priority=pr)
    html = auth_client.get(reverse("goals:list"), {"sort": "priority"}).content.decode()
    assert html.index("Highgoal") < html.index("Medgoal") < html.index("Lowgoal")


def test_goal_list_pagination_and_layouts(auth_client, user, make_goal):
    for i in range(13):
        make_goal(user, title=f"Goal {i:02d}")
    url = reverse("goals:list")
    p1 = auth_client.get(url).content.decode()
    p2 = auth_client.get(url, {"page": 2}).content.decode()
    assert "Page 1 of 2" in p1 and "Goal 12" not in p1 and "Goal 12" in p2
    assert auth_client.get(url, {"layout": "list"}).status_code == 200


def test_goal_list_query_count_does_not_grow_with_goals(auth_client, user, make_goal, make_milestone, make_task):
    url = reverse("goals:list")
    make_goal(user, title="One")
    auth_client.get(url)  # warm up session/seed queries
    with CaptureQueriesContext(connection) as small:
        auth_client.get(url)
    for i in range(8):
        g = make_goal(user, title=f"G{i}", progress_mode="task")
        make_milestone(g); make_task(user, goal=g)
    with CaptureQueriesContext(connection) as large:
        auth_client.get(url)
    assert len(large) == len(small)


def test_export_includes_phase2_data_for_owner_only(auth_client, user, other_user, make_goal, make_task, make_habit, log_habit):
    import json
    make_goal(user, title="Mine"); make_task(user, title="My task")
    h = make_habit(user, name="My habit"); log_habit(h, local_today(user))
    make_goal(other_user, title="Secret goal"); make_task(other_user, title="Secret task")
    body = auth_client.get(reverse("accounts:export")).content.decode()
    data = json.loads(body)
    assert [g["title"] for g in data["goals"]] == ["Mine"] and data["tasks"][0]["title"] == "My task"
    assert data["habits"][0]["name"] == "My habit" and len(data["habit_logs"]) == 1
    assert "Secret" not in body


def test_account_deletion_removes_all_phase2_data(client, user, other_user, password, make_goal, make_milestone, make_task, make_habit, log_habit):
    g = make_goal(user); make_milestone(g); make_task(user, goal=g); h = make_habit(user); log_habit(h, local_today(user))
    keep = make_goal(other_user)
    client.force_login(user)
    client.post(reverse("accounts:delete"), {"password": password, "confirm": "on"})
    assert not Goal.objects.filter(owner_id=user.pk).exists()
    assert not Task.objects.filter(owner_id=user.pk).exists()
    from apps.planner.models import Habit, HabitLog
    assert not Habit.objects.filter(owner_id=user.pk).exists() and not HabitLog.objects.filter(owner_id=user.pk).exists()
    assert Goal.objects.filter(pk=keep.pk).exists()
