"""Dashboard summary calculations.

All figures come from real records through the same service functions the
modules use, so totals cannot drift between pages. Finance and later modules
still show honest "coming" cards instead of invented numbers.
"""
import datetime as dt
from collections import defaultdict

from django.urls import reverse

from apps.accounts.models import LIFE_AREA_CHOICES
from apps.core.services import PRIORITY_RANK, local_now
from apps.finance.services import dashboard_finance
from apps.goals.models import Goal
from apps.goals.services import annotate_progress, attach_progress
from apps.planner import habit_services as hs
from apps.planner.models import Habit, HabitLog, Task
from apps.planner.services import OPEN_STATUSES

DAY = dt.timedelta(days=1)


def greeting_for(hour: int) -> str:
    if hour < 5:
        return "Good evening"
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


COMING_CARDS = [
    ("Future plans", "core:future_plans", "compass", "Future plans are coming in Phase 4.", "Phase 4"),
]


def _habit_rows(user, today, week_start):
    habits = list(Habit.objects.filter(owner=user, is_active=True))
    by = defaultdict(set)
    for hid, d in HabitLog.objects.filter(owner=user, habit__in=habits).values_list("habit_id", "date"):
        by[hid].add(d)
    rows = []
    for h in habits:
        if h.frequency != Habit.Frequency.WEEKLY and not hs.scheduled_on(h, today):
            continue
        if h.start_date > today or (h.end_date and h.end_date < today):
            continue
        wd, we = hs.weekly_rate(h, by[h.id], today, today, week_start)
        rows.append({
            "habit": h, "done_today": today in by[h.id], "week_done": wd, "week_expected": we,
            "streak": hs.current_streak(h, by[h.id], today, week_start),
        })
    return rows


def build_dashboard_summary(user):
    now = local_now(user)
    today = now.date()
    profile = user.profile
    area_names = dict(LIFE_AREA_CHOICES)

    open_tasks = Task.objects.filter(owner=user, status__in=OPEN_STATUSES).select_related("goal")
    ranked = open_tasks.annotate(priority_rank=PRIORITY_RANK)
    priorities = list(ranked.filter(due_date=today).order_by("priority_rank", "id")[:6])
    overdue_qs = open_tasks.filter(due_date__lt=today)
    overdue = list(overdue_qs.order_by("due_date")[:5])
    due_soon = list(ranked.filter(due_date__gt=today, due_date__lte=today + 7 * DAY).order_by("due_date", "priority_rank")[:6])
    done_today = Task.objects.filter(owner=user, status=Task.Status.COMPLETED, due_date=today).count()
    due_today_total = open_tasks.filter(due_date=today).count() + done_today

    goals = attach_progress(
        annotate_progress(Goal.objects.filter(owner=user).exclude(status__in=[Goal.Status.COMPLETED, Goal.Status.ABANDONED]))
        .select_related("life_area").order_by("target_date", "title")[:5]
    )
    habit_rows = _habit_rows(user, today, profile.week_start)

    return {
        "greeting": greeting_for(now.hour),
        "name": user.get_short_name(),
        "today": today,
        "onboarding_pending": not profile.onboarding_completed,
        "life_area_labels": [area_names[a] for a in profile.life_areas if a in area_names],
        "priorities": priorities,
        "overdue": overdue,
        "overdue_count": overdue_qs.count(),
        "due_soon": due_soon,
        "tasks_done_today": done_today,
        "tasks_due_today_total": due_today_total,
        "goals": goals,
        "finance": dashboard_finance(user, today),
        "habits": habit_rows,
        "habits_done": sum(1 for r in habit_rows if r["done_today"]),
        "has_any_data": bool(priorities or overdue or due_soon or goals or habit_rows),
        "cards": [
            {"label": label, "url": reverse(url), "icon": icon, "empty": empty, "phase": phase}
            for label, url, icon, empty, phase in COMING_CARDS
        ],
    }
