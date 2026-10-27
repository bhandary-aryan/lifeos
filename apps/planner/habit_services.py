"""Habit scheduling, streaks and consistency, derived only from HabitLog rows.

Rules
- A habit is *scheduled* on a date if it is inside its active window and matches
  its frequency (daily; selected weekdays). Weekly habits are measured per week.
- Window = [start_date, min(end_date, paused_on or today, today)].
- Today never breaks a streak: if today is scheduled but not yet logged, the
  streak is counted up to yesterday.
- Logs on unscheduled days are kept but ignored by rate/streak maths.
- Pausing stops new days counting from the pause date. Logs are never deleted.
  Known limitation: a *past* period containing a pause counts the paused days
  as not completed (pause intervals are not stored separately).
- Dates are plain local calendar dates supplied by the caller (use
  core.services.local_today(user)), so timezones are handled upstream.
"""
import calendar
import datetime as dt
from math import floor

from .models import Habit

DAY = dt.timedelta(days=1)


def window(habit, today):
    end = today
    if habit.end_date and habit.end_date < end:
        end = habit.end_date
    if habit.paused_on and habit.paused_on < end:
        end = habit.paused_on
    if end < habit.start_date:
        return None
    return habit.start_date, end


def scheduled_on(habit, d):
    if d < habit.start_date or (habit.end_date and d > habit.end_date):
        return False
    if habit.frequency == Habit.Frequency.WEEKDAYS:
        return d.weekday() in (habit.weekdays or [])
    return True  # daily, and weekly habits may be done on any day


def _scheduled_days(habit, start, end):
    d = start
    while d <= end:
        if scheduled_on(habit, d):
            yield d
        d += DAY


def week_bounds(d, week_start):
    s = d - dt.timedelta(days=(d.weekday() - week_start) % 7)
    return s, s + dt.timedelta(days=6)


# ---------- streaks ----------
def _week_counts(habit, logs, w_start, w_end, week_start):
    """{week_start_date: completions} for every week touching [w_start, w_end]."""
    counts = {}
    cur = week_bounds(w_start, week_start)[0]
    while cur <= w_end:
        counts[cur] = 0
        cur += dt.timedelta(days=7)
    for d in logs:
        if w_start <= d <= w_end:
            counts[week_bounds(d, week_start)[0]] += 1
    return counts


def current_streak(habit, logs, today, week_start=0):
    w = window(habit, today)
    if not w:
        return 0
    start, end = w
    if habit.frequency == Habit.Frequency.WEEKLY:
        counts = _week_counts(habit, logs, start, end, week_start)
        keys = sorted(counts, reverse=True)
        this_week = week_bounds(end, week_start)[0]
        streak = 0
        for k in keys:
            met = counts[k] >= habit.weekly_target
            if k == this_week and not met:
                continue  # week still in progress
            if not met:
                break
            streak += 1
        return streak
    days = list(_scheduled_days(habit, start, end))
    streak = 0
    for d in reversed(days):
        if d in logs:
            streak += 1
        elif d == today:
            continue
        else:
            break
    return streak


def best_streak(habit, logs, today, week_start=0):
    w = window(habit, today)
    if not w:
        return 0
    start, end = w
    best = run = 0
    if habit.frequency == Habit.Frequency.WEEKLY:
        counts = _week_counts(habit, logs, start, end, week_start)
        this_week = week_bounds(end, week_start)[0]
        for k in sorted(counts):
            met = counts[k] >= habit.weekly_target
            if k == this_week and not met:
                continue
            run = run + 1 if met else 0
            best = max(best, run)
        return best
    for d in _scheduled_days(habit, start, end):
        if d in logs:
            run += 1
            best = max(best, run)
        elif d == today:
            continue
        else:
            run = 0
    return best


# ---------- rates ----------
def _range_stats(habit, logs, start, end, today):
    """(done, expected) for [start, end] clipped to the habit window."""
    w = window(habit, today)
    if not w:
        return 0, 0
    s, e = max(start, w[0]), min(end, w[1])
    if s > e:
        return 0, 0
    if habit.frequency == Habit.Frequency.WEEKLY:
        days = (e - s).days + 1
        expected = max(1, floor(habit.weekly_target * days / 7 + 0.5))
        done = sum(1 for d in logs if s <= d <= e)
        return min(done, expected), expected
    sched = list(_scheduled_days(habit, s, e))
    return sum(1 for d in sched if d in logs), len(sched)


def weekly_rate(habit, logs, any_day_in_week, today, week_start=0):
    ws, we = week_bounds(any_day_in_week, week_start)
    if habit.frequency == Habit.Frequency.WEEKLY:
        w = window(habit, today)
        if not w or we < w[0] or ws > w[1]:
            return 0, 0
        done = sum(1 for d in logs if max(ws, w[0]) <= d <= min(we, w[1]))
        return min(done, habit.weekly_target), habit.weekly_target
    return _range_stats(habit, logs, ws, we, today)


def monthly_consistency(habit, logs, year, month, today):
    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])
    return _range_stats(habit, logs, first, last, today)


def pct(done, expected):
    return round(done * 100 / expected) if expected else None


def habit_stats(habit, logs, today, week_start=0):
    wd, we = weekly_rate(habit, logs, today, today, week_start)
    md, me = monthly_consistency(habit, logs, today.year, today.month, today)
    return {
        "current_streak": current_streak(habit, logs, today, week_start),
        "best_streak": best_streak(habit, logs, today, week_start),
        "week_done": wd, "week_expected": we, "week_pct": pct(wd, we),
        "month_done": md, "month_expected": me, "month_pct": pct(md, me),
        "total_logs": len(logs),
    }
