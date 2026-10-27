"""Goal progress and life-area helpers.

Progress modes (one per goal):
- manual:    the stored percentage (0-100).
- milestone: weight of completed milestones / weight of all milestones.
- task:      weight of completed tasks / weight of all non-cancelled tasks linked
             to the goal. A task is one row, so a task linked to both a goal and
             one of its milestones is counted exactly once.
A goal with nothing to measure reports 0% (never divides by zero).
"""
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db.models import IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce

from .models import DEFAULT_LIFE_AREAS, Goal, LifeArea, Milestone


def ensure_default_life_areas(user):
    existing = {n.lower() for n in LifeArea.objects.filter(owner=user).values_list("name", flat=True)}
    missing = [LifeArea(owner=user, name=n) for n in DEFAULT_LIFE_AREAS if n.lower() not in existing]
    if missing:
        LifeArea.objects.bulk_create(missing, ignore_conflicts=True)


def percent(done, total) -> int:
    """Whole-number percentage, rounded half up, clamped to 0-100."""
    if not total or total <= 0:
        return 0
    value = (Decimal(done) * 100 / Decimal(total)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(0, min(100, int(value)))


def _sum_subquery(model, filters, outer_field):
    qs = (
        model.objects.filter(**{outer_field: OuterRef("pk")}, **filters)
        .order_by()
        .values(outer_field)
        .annotate(s=Sum("weight"))
        .values("s")
    )
    return Coalesce(Subquery(qs, output_field=IntegerField()), 0)


def annotate_progress(qs):
    """Annotate goals with the weights needed for progress, in a single query.

    Separate subqueries avoid the row multiplication a double JOIN would cause.
    """
    Task = apps.get_model("planner", "Task")
    live_tasks = {"status__in": ["todo", "in_progress", "completed"]}
    return qs.annotate(
        ms_total=_sum_subquery(Milestone, {}, "goal"),
        ms_done=_sum_subquery(Milestone, {"status": Milestone.Status.COMPLETED}, "goal"),
        task_total=_sum_subquery(Task, live_tasks, "goal"),
        task_done=_sum_subquery(Task, {"status": "completed"}, "goal"),
    )


def progress_from_annotations(goal) -> int:
    mode = goal.progress_mode
    if mode == Goal.ProgressMode.MANUAL:
        return max(0, min(100, goal.manual_progress))
    if mode == Goal.ProgressMode.MILESTONE:
        return percent(goal.ms_done, goal.ms_total)
    return percent(goal.task_done, goal.task_total)


def goal_progress(goal) -> int:
    """Progress for a single goal (one annotated query)."""
    annotated = annotate_progress(Goal.objects.filter(pk=goal.pk)).get()
    return progress_from_annotations(annotated)


def attach_progress(goals):
    """Set `.progress` on every goal in an annotated iterable and return a list."""
    out = list(goals)
    for g in out:
        g.progress = progress_from_annotations(g)
    return out
