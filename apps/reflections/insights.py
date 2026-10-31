"""Historical trends and the weekly snapshot, computed only from stored records.

All bucketing uses the USER's calendar (timezone and week start), never the server's.
Nothing here is cached or stored, so a number on the page can't disagree with the records behind it.
"""
import datetime as dt
from collections import defaultdict

from django.db.models import F, Sum

from apps.core.services import user_zone
from apps.finance.calculations import ZERO, shift_month
from apps.finance.models import SavingsContribution, Transaction
from apps.finance.services import monthly_series, period_summary
from apps.goals.models import Goal, Milestone
from apps.planner import habit_services as hs
from apps.planner.models import Habit, HabitLog, Task
from apps.planner.services import OPEN_STATUSES, week_start_for


def _local_date(value, tz):
    return value.astimezone(tz).date()


def _bounds(week_start, tz):
    """Aware datetimes for [week_start 00:00, next week 00:00) in the user's zone (DST-safe)."""
    start = dt.datetime.combine(week_start, dt.time.min, tzinfo=tz)
    end = dt.datetime.combine(week_start + dt.timedelta(days=7), dt.time.min, tzinfo=tz)
    return start, end


def month_keys(today, n):
    """The n calendar months ending with today's, oldest first, as (year, month)."""
    return [shift_month(today.year, today.month, -(n - 1 - i)) for i in range(n)]


def week_starts(today, week_start, n):
    cur = week_start_for(today, week_start)
    return [cur - dt.timedelta(days=7 * (n - 1 - i)) for i in range(n)]


# ------------------------------------------------------------------ weekly snapshot
def week_snapshot(user, week_start, today, currency):
    tz = user_zone(user)
    start_dt, end_dt = _bounds(week_start, tz)
    week_end = week_start + dt.timedelta(days=7)         # exclusive
    wsd = user.profile.week_start

    done_tasks = list(Task.objects.filter(owner=user, status=Task.Status.COMPLETED, completed_at__gte=start_dt, completed_at__lt=end_dt)
                      .select_related("goal").order_by("completed_at"))
    carried = list(Task.objects.filter(owner=user, status__in=OPEN_STATUSES, due_date__gte=week_start, due_date__lt=week_end).order_by("due_date"))
    milestones = list(Milestone.objects.filter(owner=user, status=Milestone.Status.COMPLETED, completed_at__gte=start_dt, completed_at__lt=end_dt)
                      .select_related("goal"))
    goals = list(Goal.objects.filter(owner=user, status=Goal.Status.COMPLETED, completed_at__gte=start_dt, completed_at__lt=end_dt))

    habits = list(Habit.objects.filter(owner=user))
    logs = defaultdict(set)
    for hid, d in HabitLog.objects.filter(owner=user, habit__in=habits).values_list("habit_id", "date"):
        logs[hid].add(d)
    habit_rows = []
    for h in habits:
        done, expected = hs.weekly_rate(h, logs[h.id], week_start, today, wsd)
        if expected:
            habit_rows.append({"habit": h, "done": done, "expected": expected})

    saved = SavingsContribution.objects.filter(owner=user, goal__currency=currency, date__gte=week_start, date__lt=week_end).aggregate(t=Sum("amount"))["t"] or ZERO
    return {"week_start": week_start, "week_end": week_end - dt.timedelta(days=1), "tasks_done": done_tasks, "tasks_carried": carried,
            "milestones_done": milestones, "goals_done": goals, "habits": habit_rows, "currency": currency,
            "finance": period_summary(user, currency, week_start, week_end), "saved": saved}


# ---------------------------------------------------------------------- trends
def goal_trend(user, today, months=12):
    tz = user_zone(user)
    keys = month_keys(today, months)
    first = dt.datetime.combine(dt.date(*keys[0], 1), dt.time.min, tzinfo=tz)
    goals, mils = defaultdict(int), defaultdict(int)
    for c in Goal.objects.filter(owner=user, status=Goal.Status.COMPLETED, completed_at__gte=first).values_list("completed_at", flat=True):
        d = _local_date(c, tz); goals[(d.year, d.month)] += 1
    for c in Milestone.objects.filter(owner=user, status=Milestone.Status.COMPLETED, completed_at__gte=first).values_list("completed_at", flat=True):
        d = _local_date(c, tz); mils[(d.year, d.month)] += 1
    rows = [{"label": dt.date(y, m, 1).strftime("%b %Y"), "goals": goals[(y, m)], "milestones": mils[(y, m)]} for y, m in keys]
    live = Goal.objects.filter(owner=user).exclude(status__in=[Goal.Status.COMPLETED, Goal.Status.ABANDONED]).count()
    return {"rows": rows, "active": live, "completed_total": Goal.objects.filter(owner=user, status=Goal.Status.COMPLETED).count()}


def task_trend(user, today, weeks=12):
    tz = user_zone(user)
    wsd = user.profile.week_start
    starts = week_starts(today, wsd, weeks)
    first_dt, _ = _bounds(starts[0], tz)
    done = defaultdict(int)
    for c in Task.objects.filter(owner=user, status=Task.Status.COMPLETED, completed_at__gte=first_dt).values_list("completed_at", flat=True):
        done[week_start_for(_local_date(c, tz), wsd)] += 1
    due, due_done = defaultdict(int), defaultdict(int)
    qs = (Task.objects.filter(owner=user, due_date__gte=starts[0], due_date__lt=starts[-1] + dt.timedelta(days=7))
          .exclude(status=Task.Status.CANCELLED).values_list("due_date", "status"))
    for d, status in qs:
        k = week_start_for(d, wsd)
        due[k] += 1
        due_done[k] += status == Task.Status.COMPLETED
    rows = [{"label": s.strftime("%-d %b"), "week_start": s, "completed": done[s], "due": due[s],
             "rate": round(due_done[s] * 100 / due[s]) if due[s] else None} for s in starts]
    return {"rows": rows}


def habit_trend(user, today, weeks=12):
    wsd = user.profile.week_start
    starts = week_starts(today, wsd, weeks)
    habits = list(Habit.objects.filter(owner=user))
    logs = defaultdict(set)
    for hid, d in HabitLog.objects.filter(owner=user, habit__in=habits).values_list("habit_id", "date"):
        logs[hid].add(d)
    rows = []
    for s in starts:
        done = expected = 0
        for h in habits:
            d_, e_ = hs.weekly_rate(h, logs[h.id], s, today, wsd)
            done += d_; expected += e_
        rows.append({"label": s.strftime("%-d %b"), "done": done, "expected": expected, "pct": hs.pct(done, expected)})
    per_habit = []
    for h in habits:
        st = hs.habit_stats(h, logs[h.id], today, wsd)
        per_habit.append({"habit": h, **st})
    return {"rows": rows, "per_habit": per_habit}


def savings_trend(user, currency, today, months=12):
    keys = month_keys(today, months)
    first = dt.date(*keys[0], 1)
    base = SavingsContribution.objects.filter(owner=user, goal__currency=currency)
    before = base.filter(date__lt=first).aggregate(t=Sum("amount"))["t"] or ZERO
    by = defaultdict(lambda: ZERO)
    for d, a in base.filter(date__gte=first).values_list("date", "amount"):
        by[(d.year, d.month)] += a
    rows, running = [], before
    for y, m in keys:
        running += by[(y, m)]
        rows.append({"label": dt.date(y, m, 1).strftime("%b %Y"), "contributed": by[(y, m)], "cumulative": running})
    return {"rows": rows}


def spending_trend(user, currency, today, months=12):
    return {"rows": monthly_series(user, currency, today.year, today.month, n=months)}
