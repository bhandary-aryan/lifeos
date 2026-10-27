import pytest
from django.contrib.auth import get_user_model

PASSWORD = "S3cure-pass-phrase!"


@pytest.fixture
def password():
    return PASSWORD


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("alice@example.com", PASSWORD)


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user("bob@example.com", PASSWORD)


@pytest.fixture
def auth_client(client, user):
    client.force_login(user)
    return client


import datetime as dt  # noqa: E402

from apps.core.services import local_today  # noqa: E402
from apps.goals.models import Goal, Milestone  # noqa: E402
from apps.planner.models import Habit, HabitLog, Task  # noqa: E402


@pytest.fixture
def make_goal(db):
    def _make(owner, **kw):
        kw.setdefault("title", "Goal")
        return Goal.objects.create(owner=owner, **kw)
    return _make


@pytest.fixture
def make_milestone(db):
    def _make(goal, **kw):
        kw.setdefault("title", "Milestone")
        return Milestone.objects.create(owner=goal.owner, goal=goal, **kw)
    return _make


@pytest.fixture
def make_task(db):
    def _make(owner, **kw):
        kw.setdefault("title", "Task")
        return Task.objects.create(owner=owner, **kw)
    return _make


@pytest.fixture
def make_habit(db):
    def _make(owner, **kw):
        kw.setdefault("name", "Habit")
        kw.setdefault("start_date", local_today(owner) - dt.timedelta(days=60))
        return Habit.objects.create(owner=owner, **kw)
    return _make


@pytest.fixture
def log_habit(db):
    def _log(habit, *dates):
        for d in dates:
            HabitLog.objects.create(owner=habit.owner, habit=habit, date=d)
    return _log
