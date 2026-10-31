"""In-app reminders, computed on demand from real records (nothing is stored or pushed).

Respects the user's preferences: reminders can be switched off and the look-ahead window is theirs.
Email/push delivery, quiet hours and per-channel frequency belong to Phase 6 (they need a scheduler).
"""
import datetime as dt

from django.urls import reverse

from apps.finance.models import Debt
from apps.finance.services import next_due_date
from apps.future_plans.models import PlanAction
from apps.goals.models import Goal, Milestone
from apps.planner.models import Task
from apps.planner.services import OPEN_STATUSES, week_start_for
from apps.reflections.models import WeeklyReview

URGENT, SOON, INFO = 0, 1, 2


def _item(level, kind, title, detail, when, url):
    return {"level": level, "kind": kind, "title": title, "detail": detail, "when": when, "url": url}


def _rel(d, today):
    n = (d - today).days
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days" if n > 1 else f"{-n} day{'s' if n != -1 else ''} overdue"


def build_reminders(user, today):
    p = user.profile
    if not p.reminders_enabled:
        return []
    horizon = today + dt.timedelta(days=p.reminder_lead_days)
    out = []

    for t in Task.objects.filter(owner=user, status__in=OPEN_STATUSES, due_date__lte=horizon).order_by("due_date")[:30]:
        level = URGENT if t.due_date <= today else SOON
        out.append(_item(level, "task", t.title, f"Task · due {_rel(t.due_date, today)}", t.due_date, reverse("planner:task_update", args=[t.pk])))

    for m in Milestone.objects.filter(owner=user, due_date__isnull=False, due_date__lte=horizon).exclude(status=Milestone.Status.COMPLETED).select_related("goal")[:20]:
        if m.goal.status in (Goal.Status.COMPLETED, Goal.Status.ABANDONED):
            continue
        out.append(_item(URGENT if m.due_date < today else SOON, "milestone", m.title, f"Milestone of “{m.goal.title}” · due {_rel(m.due_date, today)}",
                         m.due_date, reverse("goals:detail", args=[m.goal_id])))

    for g in Goal.objects.filter(owner=user, target_date__isnull=False, target_date__lte=horizon).exclude(status__in=[Goal.Status.COMPLETED, Goal.Status.ABANDONED])[:20]:
        out.append(_item(URGENT if g.target_date < today else SOON, "goal", g.title, f"Goal target date {_rel(g.target_date, today)}", g.target_date,
                         reverse("goals:detail", args=[g.pk])))

    for d in Debt.objects.filter(owner=user, status=Debt.Status.ACTIVE, due_day__isnull=False, minimum_payment__gt=0):
        due = next_due_date(d.due_day, today)
        if due and due <= horizon:
            out.append(_item(URGENT if due == today else SOON, "payment", f"Payment to {d.creditor}", f"Minimum payment due {_rel(due, today)}", due,
                             reverse("finance:debt_detail", args=[d.pk])))

    for a in PlanAction.objects.filter(owner=user, is_done=False, due_date__isnull=False, due_date__lte=horizon).select_related("plan")[:20]:
        out.append(_item(URGENT if a.due_date < today else SOON, "plan", a.title, f"Step in “{a.plan.title}” · due {_rel(a.due_date, today)}", a.due_date,
                         reverse("future_plans:detail", args=[a.plan_id])))

    ws = week_start_for(today, p.week_start)
    if today >= ws + dt.timedelta(days=4) and not WeeklyReview.objects.filter(owner=user, week_start=ws).exists():
        out.append(_item(INFO, "review", "Weekly review", "Optional — a few minutes to look back on this week", ws, reverse("reflections:review_week", args=[ws.isoformat()])))

    return sorted(out, key=lambda i: (i["level"], i["when"], i["title"].lower()))[:50]
