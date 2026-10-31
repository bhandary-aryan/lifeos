import datetime as dt
import json
from decimal import Decimal as D

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.services import local_today
from apps.finance.models import SavingsContribution, Transaction
from apps.goals.models import Goal, Milestone
from apps.planner.models import Task
from apps.reflections import insights as ins
from apps.reflections.models import JournalEntry, WeeklyReview

pytestmark = pytest.mark.django_db
UTC = dt.timezone.utc
DAY = dt.timedelta(days=1)
T = Transaction.Type


def at(y, m, d, h=12, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=UTC)


def set_completed(obj, when):
    type(obj).objects.filter(pk=obj.pk).update(completed_at=when)


@pytest.fixture
def make_review(db):
    def _make(owner, week_start, **kw):
        kw.setdefault("accomplishments", "Did things")
        return WeeklyReview.objects.create(owner=owner, week_start=week_start, **kw)
    return _make


@pytest.fixture
def make_entry(db):
    def _make(owner, **kw):
        kw.setdefault("date", dt.date(2026, 3, 1)); kw.setdefault("body", "text")
        return JournalEntry.objects.create(owner=owner, **kw)
    return _make


# ---------------------------------------------------------------- models
def test_review_unique_per_week_and_needs_content(user, make_review):
    make_review(user, dt.date(2026, 3, 2))
    with pytest.raises(IntegrityError), transaction.atomic():
        make_review(user, dt.date(2026, 3, 2))
    with pytest.raises(ValidationError):
        WeeklyReview(owner=user, week_start=dt.date(2026, 3, 9), accomplishments="   ").full_clean()
    assert WeeklyReview(owner=user, week_start=dt.date(2026, 3, 9), lessons="x", obstacles=" ").filled_sections == 1


def test_journal_entry_rules(user):
    with pytest.raises(ValidationError):
        JournalEntry(owner=user, date=dt.date(2026, 1, 1), body="   ").full_clean()
    assert JournalEntry(owner=user, date=dt.date(2026, 1, 1), body="one two  three").word_count == 3


def test_two_users_can_review_the_same_week(user, other_user, make_review):
    make_review(user, dt.date(2026, 3, 2)); make_review(other_user, dt.date(2026, 3, 2))
    assert WeeklyReview.objects.count() == 2


# ------------------------------------------------------- period helpers
def test_month_keys_and_week_starts():
    assert ins.month_keys(dt.date(2026, 2, 10), 3) == [(2025, 12), (2026, 1), (2026, 2)]
    assert ins.month_keys(dt.date(2026, 6, 1), 1) == [(2026, 6)]
    ws = ins.week_starts(dt.date(2026, 3, 18), 0, 3)                      # Wednesday, Monday-start
    assert ws == [dt.date(2026, 3, 2), dt.date(2026, 3, 9), dt.date(2026, 3, 16)]
    assert ins.week_starts(dt.date(2026, 3, 18), 6, 2) == [dt.date(2026, 3, 8), dt.date(2026, 3, 15)]    # Sunday-start


# ------------------------------------------------------- weekly snapshot
TODAY = dt.date(2026, 3, 18)
WEEK = dt.date(2026, 3, 2)


def test_snapshot_task_window_boundaries_are_exact(user, make_task):
    inside = [at(2026, 3, 2, 0, 0), at(2026, 3, 5), at(2026, 3, 8, 23, 59)]
    outside = [at(2026, 3, 1, 23, 59), at(2026, 3, 9, 0, 0)]
    for i, when in enumerate(inside + outside):
        t = make_task(user, title=f"t{i}", status="completed"); set_completed(t, when)
    open_t = make_task(user, title="still open", status="todo", due_date=dt.date(2026, 3, 4))
    done_due = make_task(user, title="done and due", status="completed", due_date=dt.date(2026, 3, 4)); set_completed(done_due, at(2099, 1, 1))
    cancelled = make_task(user, title="cancelled", status="cancelled", due_date=dt.date(2026, 3, 5))
    snap = ins.week_snapshot(user, WEEK, TODAY, "GBP")
    assert sorted(t.title for t in snap["tasks_done"]) == ["t0", "t1", "t2"]
    assert [t.title for t in snap["tasks_carried"]] == ["still open"]
    assert snap["week_start"] == WEEK and snap["week_end"] == dt.date(2026, 3, 8)
    del open_t, cancelled


def test_snapshot_week_boundaries_follow_dst_in_users_timezone(user, make_task):
    """London springs forward on 29 March 2026: 23:30Z on the 29th is already 00:30 on the 30th."""
    user.profile.timezone = "Europe/London"; user.profile.save()
    a = make_task(user, title="Sunday night GMT", status="completed"); set_completed(a, at(2026, 3, 22, 23, 30))      # 23:30 GMT, Sunday 22nd
    b = make_task(user, title="Monday just after midnight BST", status="completed"); set_completed(b, at(2026, 3, 29, 23, 30))   # 00:30 BST, Monday 30th
    c = make_task(user, title="Sunday late BST", status="completed"); set_completed(c, at(2026, 3, 29, 22, 30))       # 23:30 BST, Sunday 29th
    prev = ins.week_snapshot(user, dt.date(2026, 3, 23), dt.date(2026, 4, 10), "GBP")["tasks_done"]
    nxt = ins.week_snapshot(user, dt.date(2026, 3, 30), dt.date(2026, 4, 10), "GBP")["tasks_done"]
    earlier = ins.week_snapshot(user, dt.date(2026, 3, 16), dt.date(2026, 4, 10), "GBP")["tasks_done"]
    assert [t.title for t in earlier] == ["Sunday night GMT"]
    assert sorted(t.title for t in prev) == ["Sunday late BST"]
    assert [t.title for t in nxt] == ["Monday just after midnight BST"]


def test_snapshot_uses_users_timezone_not_utc(user, make_task):
    user.profile.timezone = "Pacific/Auckland"; user.profile.save()          # UTC+13 in March
    t = make_task(user, title="NZ morning", status="completed"); set_completed(t, at(2026, 3, 1, 12, 0))   # 01:00 on 2 March in NZ
    assert [x.title for x in ins.week_snapshot(user, WEEK, TODAY, "GBP")["tasks_done"]] == ["NZ morning"]


def test_snapshot_milestones_goals_habits_money_and_savings(user, make_goal, make_milestone, make_habit, log_habit, make_account, make_tx, make_savings):
    g = make_goal(user, title="Done goal", status="completed"); set_completed(g, at(2026, 3, 4))
    m = make_milestone(g, title="Reached", status="completed"); set_completed(m, at(2026, 3, 3))
    old = make_milestone(g, title="Old", status="completed"); set_completed(old, at(2026, 2, 3))
    h = make_habit(user, name="Daily", start_date=dt.date(2026, 1, 1))
    log_habit(h, dt.date(2026, 3, 2), dt.date(2026, 3, 3), dt.date(2026, 3, 4), dt.date(2026, 3, 9))        # last one is next week
    acc = make_account(user, opening="0")
    make_tx(user, T.INCOME, "100", acc, date=dt.date(2026, 3, 8)); make_tx(user, T.INCOME, "999", acc, date=dt.date(2026, 3, 9))
    make_tx(user, T.EXPENSE, "30", acc, date=dt.date(2026, 3, 2)); make_tx(user, T.EXPENSE, "999", acc, date=dt.date(2026, 3, 1))
    sg = make_savings(user)
    SavingsContribution.objects.create(owner=user, goal=sg, date=dt.date(2026, 3, 5), amount=D("25"))
    SavingsContribution.objects.create(owner=user, goal=sg, date=dt.date(2026, 3, 9), amount=D("500"))
    snap = ins.week_snapshot(user, WEEK, TODAY, "GBP")
    assert [x.title for x in snap["milestones_done"]] == ["Reached"] and [x.title for x in snap["goals_done"]] == ["Done goal"]
    assert [(r["habit"].name, r["done"], r["expected"]) for r in snap["habits"]] == [("Daily", 3, 7)]
    assert (snap["finance"]["income"], snap["finance"]["spending"]) == (D("100.00"), D("30.00")) and snap["saved"] == D("25.00")


def test_snapshot_ignores_other_users(user, other_user, make_task, make_habit, make_account, make_tx):
    t = make_task(other_user, status="completed"); set_completed(t, at(2026, 3, 3))
    make_habit(other_user, start_date=dt.date(2026, 1, 1))
    make_tx(other_user, T.INCOME, "500", make_account(other_user), date=dt.date(2026, 3, 3))
    snap = ins.week_snapshot(user, WEEK, TODAY, "GBP")
    assert snap["tasks_done"] == [] and snap["habits"] == [] and snap["finance"]["income"] == D("0.00")


# ------------------------------------------------------------- trends
def test_goal_trend_buckets_by_local_month(user, make_goal, make_milestone):
    t = dt.date(2026, 3, 18)
    for when in (at(2026, 3, 1), at(2026, 3, 31), at(2026, 2, 28), at(2025, 4, 2), at(2025, 3, 1)):          # last one is outside 12 months
        g = make_goal(user, status="completed"); set_completed(g, when)
    g2 = make_goal(user, status="abandoned"); g3 = make_goal(user, status="in_progress"); make_goal(user, status="on_hold")
    m = make_milestone(g3, status="completed"); set_completed(m, at(2026, 1, 10))
    tr = ins.goal_trend(user, t)
    by = {r["label"]: r for r in tr["rows"]}
    assert len(tr["rows"]) == 12 and tr["rows"][0]["label"] == "Apr 2025" and tr["rows"][-1]["label"] == "Mar 2026"
    assert by["Mar 2026"]["goals"] == 2 and by["Feb 2026"]["goals"] == 1 and by["Apr 2025"]["goals"] == 1 and by["Jan 2026"]["milestones"] == 1
    assert tr["active"] == 2 and tr["completed_total"] == 5                       # abandoned/completed excluded from active; total includes the old one
    del g2


def test_goal_trend_empty_is_all_zero_not_error(user):
    tr = ins.goal_trend(user, dt.date(2026, 3, 18))
    assert tr["active"] == 0 and all(r["goals"] == 0 and r["milestones"] == 0 for r in tr["rows"])


def test_task_trend_counts_and_rates(user, make_task):
    wk = dt.date(2026, 3, 16)                                    # current week (Mon)
    done = make_task(user, status="completed", due_date=wk); set_completed(done, at(2026, 3, 17))
    make_task(user, status="todo", due_date=wk + DAY)
    make_task(user, status="cancelled", due_date=wk + 2 * DAY)                      # excluded from "due"
    early = make_task(user, status="completed", due_date=None); set_completed(early, at(2026, 3, 10))
    ancient = make_task(user, status="completed"); set_completed(ancient, at(2025, 1, 1))
    tr = ins.task_trend(user, dt.date(2026, 3, 18))
    rows = {r["week_start"]: r for r in tr["rows"]}
    assert len(tr["rows"]) == 12 and tr["rows"][-1]["week_start"] == wk
    assert (rows[wk]["completed"], rows[wk]["due"], rows[wk]["rate"]) == (1, 2, 50)
    assert rows[dt.date(2026, 3, 9)]["completed"] == 1 and rows[dt.date(2026, 3, 9)]["rate"] is None      # nothing due -> no rate, not 0%
    assert sum(r["completed"] for r in tr["rows"]) == 2


def test_task_trend_respects_sunday_week_start(user, make_task):
    user.profile.week_start = 6; user.profile.save()
    t = make_task(user, status="completed"); set_completed(t, at(2026, 3, 15, 10))          # a Sunday = first day of its week
    tr = ins.task_trend(user, dt.date(2026, 3, 18))
    assert {r["week_start"]: r["completed"] for r in tr["rows"]}[dt.date(2026, 3, 15)] == 1


def test_habit_trend_percentages_and_per_habit_stats(user, make_habit, log_habit):
    today = dt.date(2026, 3, 18)
    h = make_habit(user, name="Run", start_date=dt.date(2026, 3, 2))
    log_habit(h, dt.date(2026, 3, 2), dt.date(2026, 3, 3), dt.date(2026, 3, 4), dt.date(2026, 3, 5), dt.date(2026, 3, 6), dt.date(2026, 3, 7), dt.date(2026, 3, 8))
    log_habit(h, dt.date(2026, 3, 16), dt.date(2026, 3, 17))
    tr = ins.habit_trend(user, today)
    by = {r["label"]: r for r in tr["rows"]}
    assert by["2 Mar"]["pct"] == 100 and by["9 Mar"]["pct"] == 0 and by["16 Mar"]["done"] == 2 and by["16 Mar"]["expected"] == 3        # Mon-Wed so far
    assert by["26 Jan"]["pct"] is None                                                                                        # before the habit existed
    st = tr["per_habit"][0]
    assert st["habit"].name == "Run" and st["best_streak"] == 7 and st["current_streak"] == 2


def test_habit_trend_without_habits(user):
    tr = ins.habit_trend(user, dt.date(2026, 3, 18))
    assert tr["per_habit"] == [] and all(r["pct"] is None for r in tr["rows"])


def test_savings_trend_cumulative_and_currency_filter(user, make_savings):
    gbp, usd = make_savings(user, "G"), make_savings(user, "U", currency="USD")
    for goal, d, a in ((gbp, dt.date(2025, 1, 5), "100"), (gbp, dt.date(2026, 1, 10), "50"), (gbp, dt.date(2026, 3, 1), "25"), (usd, dt.date(2026, 3, 2), "999")):
        SavingsContribution.objects.create(owner=user, goal=goal, date=d, amount=D(a))
    rows = ins.savings_trend(user, "GBP", dt.date(2026, 3, 18))["rows"]
    by = {r["label"]: r for r in rows}
    assert rows[0]["cumulative"] == D("100.00") and by["Jan 2026"]["cumulative"] == D("150.00") and by["Mar 2026"]["cumulative"] == D("175.00")
    assert by["Mar 2026"]["contributed"] == D("25.00") and by["Feb 2026"]["contributed"] == D("0.00")
    assert [r["cumulative"] for r in rows] == sorted(r["cumulative"] for r in rows)
    assert ins.savings_trend(user, "USD", dt.date(2026, 3, 18))["rows"][-1]["cumulative"] == D("999.00")


def test_spending_trend_matches_finance_series(user, make_account, make_tx):
    a = make_account(user)
    make_tx(user, T.EXPENSE, "40", a, date=dt.date(2026, 3, 3)); make_tx(user, T.INCOME, "100", a, date=dt.date(2026, 2, 3))
    rows = ins.spending_trend(user, "GBP", dt.date(2026, 3, 18))["rows"]
    assert len(rows) == 12 and rows[-1]["spending"] == D("40.00") and rows[-2]["income"] == D("100.00")


# ------------------------------------------------------------ review views
def test_review_list_empty_state_and_cta(auth_client, user):
    h = auth_client.get(reverse("reflections:list")).content.decode()
    assert "No reviews yet" in h and "always optional" in h and "Start this week" in h


def test_get_review_page_creates_nothing_and_prefills_nothing(auth_client, user, make_task):
    t = make_task(user, title="Snapshot task", status="completed")
    ws = auth_client.get(reverse("reflections:list")).context["current_week"]
    h = auth_client.get(reverse("reflections:review_week", args=[ws.isoformat()])).content.decode()
    assert not WeeklyReview.objects.exists() and "Snapshot task" in h and "nothing is filled in for you" in h
    assert "<textarea" in h and "Did things" not in h


def test_save_review_then_update_it(auth_client, user):
    ws = local_today(user) - dt.timedelta(days=local_today(user).weekday())
    url = reverse("reflections:review_week", args=[ws.isoformat()])
    r = auth_client.post(url, {"accomplishments": "Shipped phase 5", "lessons": "Tests first"})
    assert r.status_code == 302 and r.url == url
    rev = WeeklyReview.objects.get(owner=user)
    assert (rev.week_start, rev.accomplishments, rev.lessons) == (ws, "Shipped phase 5", "Tests first")
    auth_client.post(url, {"accomplishments": "Shipped and polished", "next_priorities": "Rest"})
    rev.refresh_from_db()
    assert WeeklyReview.objects.count() == 1 and rev.accomplishments == "Shipped and polished" and rev.lessons == "" and rev.next_priorities == "Rest"
    assert "2 of 8 sections written" in auth_client.get(reverse("reflections:list")).content.decode()


def test_empty_review_rejected_and_length_limited(auth_client, user):
    ws = (local_today(user) - dt.timedelta(days=local_today(user).weekday())).isoformat()
    url = reverse("reflections:review_week", args=[ws])
    r = auth_client.post(url, {"accomplishments": "   "})
    assert r.status_code == 200 and "at least one section" in r.content.decode() and not WeeklyReview.objects.exists()
    r = auth_client.post(url, {"lessons": "x" * 4001})
    assert r.status_code == 200 and not WeeklyReview.objects.exists()
    assert auth_client.post(url, {"lessons": "x" * 4000}).status_code == 302


def test_week_in_url_is_normalised_and_future_weeks_blocked(auth_client, user):
    today = local_today(user)
    ws = today - dt.timedelta(days=today.weekday())
    mid = reverse("reflections:review_week", args=[(ws + 2 * DAY).isoformat()])
    r = auth_client.get(mid)
    assert r.status_code == 302 and r.url == reverse("reflections:review_week", args=[ws.isoformat()])
    r = auth_client.get(reverse("reflections:review_week", args=[(ws + dt.timedelta(days=14)).isoformat()]))
    assert r.url == reverse("reflections:review_week", args=[ws.isoformat()])
    r = auth_client.get(reverse("reflections:review_week", args=["garbage"]))
    assert r.url == reverse("reflections:review_week", args=[ws.isoformat()])
    assert auth_client.get(reverse("reflections:review_week", args=[(ws - dt.timedelta(days=7)).isoformat()])).status_code == 200


def test_sunday_week_start_normalisation(auth_client, user):
    user.profile.week_start = 6; user.profile.save()
    today = local_today(user)
    ws = today - dt.timedelta(days=(today.weekday() + 1) % 7)
    assert ws.weekday() == 6
    assert auth_client.get(reverse("reflections:review_week", args=[ws.isoformat()])).status_code == 200


def test_delete_review_with_confirmation(auth_client, user, make_review):
    ws = local_today(user) - dt.timedelta(days=local_today(user).weekday())
    r = make_review(user, ws - dt.timedelta(days=7))
    assert auth_client.get(reverse("reflections:review_delete", args=[r.pk])).status_code == 200 and WeeklyReview.objects.exists()
    auth_client.post(reverse("reflections:review_delete", args=[r.pk]))
    assert not WeeklyReview.objects.exists()


def test_review_list_pagination(auth_client, user, make_review):
    base = dt.date(2024, 1, 1)
    for i in range(14):
        make_review(user, base + dt.timedelta(days=7 * i))
    assert "Page 1 of 2" in auth_client.get(reverse("reflections:list")).content.decode()


# ------------------------------------------------------------ journal views
def test_journal_crud_and_validation(auth_client, user):
    assert "Your private journal" in auth_client.get(reverse("reflections:journal")).content.decode()
    r = auth_client.post(reverse("reflections:journal_create"), {"date": "2026-03-01", "title": "Good day", "body": "Went for a long walk."})
    e = JournalEntry.objects.get(owner=user)
    assert r.status_code == 302 and r.url == reverse("reflections:journal_detail", args=[e.pk])
    assert "Went for a long walk." in auth_client.get(r.url).content.decode()
    auth_client.post(reverse("reflections:journal_update", args=[e.pk]), {"date": "2026-03-01", "title": "Great day", "body": "Walk, then dinner."})
    e.refresh_from_db(); assert (e.title, e.body) == ("Great day", "Walk, then dinner.")
    assert auth_client.post(reverse("reflections:journal_create"), {"date": "2026-03-02", "body": "  "}).status_code == 200
    assert auth_client.post(reverse("reflections:journal_create"), {"date": "nope", "body": "x"}).status_code == 200
    assert JournalEntry.objects.count() == 1
    assert auth_client.get(reverse("reflections:journal_delete", args=[e.pk])).status_code == 200 and JournalEntry.objects.exists()
    auth_client.post(reverse("reflections:journal_delete", args=[e.pk]))
    assert not JournalEntry.objects.exists()


def test_journal_list_shows_titles_not_text_and_search_works(auth_client, user, make_entry):
    make_entry(user, title="Morning pages", body="SECRETBODYMARKER one two three", date=dt.date(2026, 3, 1))
    make_entry(user, title="", body="plain words here", date=dt.date(2026, 2, 1))
    h = auth_client.get(reverse("reflections:journal")).content.decode()
    assert "Morning pages" in h and "Untitled entry" in h and "SECRETBODYMARKER" not in h and "4 words" in h and "3 words" in h
    found = auth_client.get(reverse("reflections:journal"), {"q": "secretbodymarker"}).content.decode()
    assert "Morning pages" in found and "Untitled entry" not in found and "SECRETBODYMARKER" not in found
    ranged = auth_client.get(reverse("reflections:journal"), {"from": "2026-02-15", "to": "2026-03-15"}).content.decode()
    assert "Morning pages" in ranged and "Untitled entry" not in ranged
    assert "No entries match" in auth_client.get(reverse("reflections:journal"), {"q": "zzzz"}).content.decode()


def test_journal_pagination(auth_client, user, make_entry):
    for i in range(22):
        make_entry(user, title=f"E{i:02d}", date=dt.date(2026, 1, 1) + dt.timedelta(days=i))
    h = auth_client.get(reverse("reflections:journal")).content.decode()
    assert "Page 1 of 2" in h and h.index("E21") < h.index("E20")                          # newest first


# ---------------------------------------------------------------- privacy
def test_private_text_never_appears_on_dashboard_reminders_or_insights(auth_client, user, make_review, make_entry):
    ws = local_today(user) - dt.timedelta(days=local_today(user).weekday())
    make_review(user, ws, accomplishments="REVIEWSECRETMARKER", lessons="REVIEWSECRETMARKER2")
    make_entry(user, title="TITLESECRETMARKER", body="JOURNALSECRETMARKER")
    for name in ("dashboard:home", "dashboard:reminders", "reflections:insights", "accounts:settings", "reflections:list", "goals:list", "planner:today", "finance:overview"):
        body = auth_client.get(reverse(name)).content.decode()
        for marker in ("REVIEWSECRETMARKER", "JOURNALSECRETMARKER", "TITLESECRETMARKER"):
            assert marker not in body, (name, marker)


def test_reviews_and_journal_are_in_the_data_export_and_deleted_with_the_account(auth_client, client, user, other_user, password, make_review, make_entry):
    make_review(user, dt.date(2026, 3, 2), lessons="MYLESSON"); make_entry(user, body="MYENTRY")
    make_review(other_user, dt.date(2026, 3, 2), lessons="BOBLESSON"); make_entry(other_user, body="BOBENTRY")
    raw = auth_client.get(reverse("accounts:export")).content.decode()
    data = json.loads(raw)
    assert data["weekly_reviews"][0]["lessons"] == "MYLESSON" and data["journal_entries"][0]["body"] == "MYENTRY" and "BOB" not in raw
    assert data["profile"]["reminders_enabled"] is True and data["profile"]["reminder_lead_days"] == 3
    client.force_login(user)
    client.post(reverse("accounts:delete"), {"password": password, "confirm": "on"})
    assert not WeeklyReview.objects.filter(owner_id=user.pk).exists() and not JournalEntry.objects.filter(owner_id=user.pk).exists()
    assert WeeklyReview.objects.filter(owner=other_user).exists() and JournalEntry.objects.filter(owner=other_user).exists()


# ---------------------------------------------------------------- ownership
def test_other_user_gets_404_and_nothing_changes(other_user, user, make_review, make_entry):
    r, e = make_review(user, dt.date(2026, 3, 2), lessons="ALICEPRIVATE"), make_entry(user, title="ALICEJOURNAL", body="ALICEPRIVATE")
    bob = Client(); bob.force_login(other_user)
    for url in (reverse("reflections:review_delete", args=[r.pk]), reverse("reflections:journal_detail", args=[e.pk]),
                reverse("reflections:journal_update", args=[e.pk]), reverse("reflections:journal_delete", args=[e.pk])):
        assert bob.get(url).status_code == 404, url
    for url in (reverse("reflections:review_delete", args=[r.pk]), reverse("reflections:journal_update", args=[e.pk]), reverse("reflections:journal_delete", args=[e.pk])):
        assert bob.post(url, {"title": "HACKED", "body": "HACKED", "date": "2026-03-01", "confirm": "on"}).status_code == 404, url
    r.refresh_from_db(); e.refresh_from_db()
    assert e.body == "ALICEPRIVATE" and WeeklyReview.objects.filter(pk=r.pk).exists()
    for name in ("reflections:list", "reflections:journal", "reflections:insights", "dashboard:home"):
        body = bob.get(reverse(name), {"q": "private"}).content.decode()           # a term that matches Alice's text but is not itself a marker
        assert "ALICEPRIVATE" not in body and "ALICEJOURNAL" not in body, name
    page = bob.get(reverse("reflections:review_week", args=[dt.date(2026, 3, 2).isoformat()])).content.decode()
    assert "ALICEPRIVATE" not in page                              # same week, different owner: Bob sees his own (empty) review


def test_anonymous_and_csrf(client, user, make_review, make_entry):
    r, e = make_review(user, dt.date(2026, 3, 2)), make_entry(user)
    urls = [reverse("reflections:list"), reverse("reflections:journal"), reverse("reflections:insights"), reverse("reflections:journal_create"),
            reverse("reflections:journal_detail", args=[e.pk]), reverse("reflections:review_week", args=["2026-03-02"]), reverse("reflections:review_delete", args=[r.pk]),
            reverse("dashboard:reminders"), reverse("dashboard:reminders_badge")]
    for url in urls:
        resp = client.get(url)
        assert resp.status_code == 302 and resp.url.startswith(reverse("accounts:login")), url
    c = Client(enforce_csrf_checks=True); c.force_login(user)
    for url in (reverse("reflections:journal_create"), reverse("reflections:review_week", args=["2026-03-02"]), reverse("reflections:journal_delete", args=[e.pk])):
        assert c.post(url, {}).status_code == 403, url


# ---------------------------------------------------------------- insights page
def test_insights_empty_state_then_real_numbers(auth_client, user, make_task, make_goal):
    h = auth_client.get(reverse("reflections:insights")).content.decode()
    assert "Nothing to chart yet" in h and "not a made-up number" in h
    t = make_task(user, status="completed", due_date=local_today(user)); set_completed(t, timezone.now())
    g = make_goal(user, status="completed"); set_completed(g, timezone.now())
    resp = auth_client.get(reverse("reflections:insights"))
    h = resp.content.decode()
    assert "Nothing to chart yet" not in h
    data = json.loads(h.split('id="chart-data" type="application/json">')[1].split("</script>")[0])
    assert sum(data["tasks"]["completed"]) == 1 and sum(data["goals"]["goals"]) == 1 and len(data["tasks"]["labels"]) == 12 and len(data["spending"]["labels"]) == 12
    assert resp.context["goals"]["completed_total"] == 1


def test_insights_has_text_alternatives_for_charts(auth_client):
    h = auth_client.get(reverse("reflections:insights")).content.decode()
    assert h.count("<canvas") == 5 and h.count('role="img"') == 5 and h.count("Show data table") + h.count("Show weekly data") >= 5


def test_insights_currency_param(auth_client, user, make_savings):
    sg = make_savings(user, currency="USD"); SavingsContribution.objects.create(owner=user, goal=sg, date=local_today(user), amount=D("77"))
    usd = auth_client.get(reverse("reflections:insights"), {"currency": "USD"}).content.decode()
    gbp = auth_client.get(reverse("reflections:insights"), {"currency": "GBP"}).content.decode()
    assert "$77.00" in usd and "$77.00" not in gbp
    assert auth_client.get(reverse("reflections:insights"), {"currency": "XXX"}).status_code == 200


def test_insights_query_count_does_not_grow_with_data(auth_client, user, make_task, make_goal, make_habit, log_habit):
    url = reverse("reflections:insights")
    auth_client.get(url)
    with CaptureQueriesContext(connection) as small:
        auth_client.get(url)
    for i in range(15):
        t = make_task(user, status="completed", due_date=local_today(user)); set_completed(t, timezone.now())
        g = make_goal(user, status="completed"); set_completed(g, timezone.now())
        h = make_habit(user, name=f"H{i}"); log_habit(h, local_today(user))
    with CaptureQueriesContext(connection) as large:
        auth_client.get(url)
    assert len(large) <= len(small) + 3                    # a few fixed extra queries once data exists, never one per row
    assert len(large) < 40


def test_navigation_marks_reviews_section_active_on_all_three_pages(auth_client):
    for name in ("reflections:list", "reflections:journal", "reflections:insights"):
        assert 'aria-current="page"' in auth_client.get(reverse(name)).content.decode(), name
