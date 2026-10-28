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


# ---------------- finance fixtures ----------------
from decimal import Decimal as _D  # noqa: E402

from apps.finance import services as fin  # noqa: E402
from apps.finance.models import Account, Category, Debt, SavingsGoal, Transaction  # noqa: E402


def money(v):
    return _D(str(v))


@pytest.fixture
def make_account(db):
    def _make(owner, name="Current", opening="0", currency="GBP", account_type="current", **kw):
        return Account.objects.create(owner=owner, name=name, opening_balance=money(opening), currency=currency, account_type=account_type, **kw)
    return _make


@pytest.fixture
def cat(db):
    def _cat(owner, name, kind="expense"):
        return Category.objects.get(owner=owner, name=name, kind=kind)
    return _cat


@pytest.fixture
def make_tx(db):
    """Creates transactions through the real validated save path."""
    def _make(owner, type, amount, account, date=None, **kw):
        tx = Transaction(owner=owner, type=type, amount=money(amount), account=account, date=date or local_today(owner), **kw)
        for f in ("principal_amount", "interest_amount"):
            if f in kw:
                setattr(tx, f, money(kw[f]))
        return fin.save_transaction(tx)
    return _make


@pytest.fixture
def make_debt(db):
    def _make(owner, creditor="Bank loan", original="1000", currency="GBP", **kw):
        kw.setdefault("start_date", local_today(owner) - dt.timedelta(days=90))
        return Debt.objects.create(owner=owner, creditor=creditor, original_amount=money(original), currency=currency, **kw)
    return _make


@pytest.fixture
def make_savings(db):
    def _make(owner, name="Emergency fund", target="1000", **kw):
        return SavingsGoal.objects.create(owner=owner, name=name, target_amount=money(target), **kw)
    return _make
