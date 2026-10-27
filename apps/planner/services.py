"""Task queries and daily-capacity calculations."""
import datetime as dt

from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.core.services import PRIORITY_RANK, local_today
from apps.core.models import Priority

from .models import Task

OPEN_STATUSES = [Task.Status.TODO, Task.Status.IN_PROGRESS]

def with_rank(qs):
    """Annotate so 'high' sorts before 'medium' before 'low' (text order would not)."""
    return qs.annotate(priority_rank=PRIORITY_RANK)


def open_tasks(user):
    return Task.objects.filter(owner=user, status__in=OPEN_STATUSES)


def tasks_for_view(user, view, today):
    qs = Task.objects.filter(owner=user).select_related("goal", "milestone")
    if view == "today":
        return qs.filter(due_date=today)
    if view == "upcoming":
        return qs.filter(status__in=OPEN_STATUSES, due_date__gt=today)
    if view == "overdue":
        return qs.filter(status__in=OPEN_STATUSES, due_date__lt=today)
    return qs


def overdue_count(user, today):
    return Task.objects.filter(owner=user, status__in=OPEN_STATUSES, due_date__lt=today).count()


def day_load(user, day):
    """Estimated minutes of open work planned for a date, and how many tasks lack an estimate."""
    qs = Task.objects.filter(owner=user, due_date=day, status__in=OPEN_STATUSES)
    agg = qs.aggregate(minutes=Coalesce(Sum("estimated_minutes"), 0))
    unestimated = qs.filter(estimated_minutes__isnull=True).count()
    return agg["minutes"], unestimated


def week_start_for(day, week_start):
    return day - dt.timedelta(days=(day.weekday() - week_start) % 7)


def week_plan(user, start, available_minutes):
    """Seven day-buckets with tasks, planned minutes and an over-capacity flag.

    One query for all tasks in the range. Over-capacity days only carry a
    *suggestion*; nothing is rescheduled automatically.
    """
    days = [start + dt.timedelta(days=i) for i in range(7)]
    tasks = list(
        Task.objects.filter(owner=user, due_date__gte=days[0], due_date__lte=days[-1])
        .exclude(status=Task.Status.CANCELLED)
        .select_related("goal")
        .annotate(priority_rank=PRIORITY_RANK)
        .order_by("start_at", "priority_rank", "id")
    )
    buckets = {d: [] for d in days}
    for t in tasks:
        buckets[t.due_date].append(t)
    result = []
    for d in days:
        items = buckets[d]
        planned = sum((t.estimated_minutes or 0) for t in items if not t.is_done)
        result.append({
            "date": d,
            "tasks": items,
            "planned_minutes": planned,
            "available_minutes": available_minutes,
            "over_by": max(0, planned - available_minutes),
            "load_pct": min(100, round(planned * 100 / available_minutes)) if available_minutes else 0,
        })
    return result
