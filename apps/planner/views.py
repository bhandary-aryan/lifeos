import calendar
import datetime as dt
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from apps.core.services import PRIORITY_RANK, local_today, parse_date
from apps.core.views import OwnedCreateView, OwnedDeleteView, OwnedUpdateView, safe_next
from apps.core.mixins import OwnerQuerysetMixin
from apps.core.models import Priority
from apps.goals.models import Goal
from apps.goals.services import ensure_default_life_areas

from . import habit_services as hs
from .forms import HabitForm, QuickTaskForm, TaskForm
from .models import Habit, HabitLog, Task
from .services import OPEN_STATUSES, day_load, tasks_for_view, week_plan, week_start_for

DAY = dt.timedelta(days=1)

TASK_SORTS = {
    "due": [F("due_date").asc(nulls_last=True), "priority_rank", "id"],
    "priority": ["priority_rank", F("due_date").asc(nulls_last=True), "id"],
    "title": ["title"],
    "recent": ["-created_at"],
}


# ===================== TASKS =====================
class TaskListView(LoginRequiredMixin, ListView):
    """Upcoming, Overdue and All views share one list with search/filter/sort."""

    template_name = "planner/task_list.html"
    paginate_by = 25
    view_name = "all"

    def get_queryset(self):
        user = self.request.user
        g = self.request.GET
        qs = tasks_for_view(user, self.view_name, local_today(user)).annotate(priority_rank=PRIORITY_RANK)
        if q := g.get("q", "").strip():
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        if self.view_name == "all" and g.get("status") in Task.Status.values:
            qs = qs.filter(status=g["status"])
        if g.get("priority") in Priority.values:
            qs = qs.filter(priority=g["priority"])
        if g.get("goal", "").isdigit():
            qs = qs.filter(goal_id=int(g["goal"]))
        return qs.order_by(*TASK_SORTS.get(g.get("sort"), TASK_SORTS["due"]))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        params = self.request.GET.copy()
        params.pop("page", None)
        titles = {"upcoming": "Upcoming tasks", "overdue": "Overdue tasks", "all": "All tasks"}
        ctx.update(
            page_title=titles[self.view_name], tab=self.view_name, qs=params.urlencode(), f=self.request.GET,
            today=local_today(user), goals=Goal.objects.filter(owner=user),
            priorities=Priority.choices, statuses=Task.Status.choices,
            quick_form=QuickTaskForm(user=user), next_url=self.request.get_full_path(),
        )
        return ctx


class TaskCreateView(OwnedCreateView):
    model = Task
    form_class = TaskForm
    page_title = "New task"
    success_message = "Task created."
    cancel_url = reverse_lazy("planner:today")

    def get_initial(self):
        initial = super().get_initial()
        if d := parse_date(self.request.GET.get("date")):
            initial["due_date"] = d
        if self.request.GET.get("goal", "").isdigit():
            initial["goal"] = int(self.request.GET["goal"])
        return initial

    def get_default_success_url(self):
        return reverse("planner:today")


class TaskUpdateView(OwnedUpdateView):
    model = Task
    form_class = TaskForm
    page_title = "Edit task"
    success_message = "Task updated."
    cancel_url = reverse_lazy("planner:all")

    def get_default_success_url(self):
        return reverse("planner:all")


class TaskDeleteView(OwnedDeleteView):
    model = Task
    noun = "task"
    success_url = reverse_lazy("planner:all")
    cancel_url = reverse_lazy("planner:all")
    success_message = "Task deleted."


@login_required
@require_POST
def task_toggle(request, pk):
    task = get_object_or_404(Task, pk=pk, owner=request.user)
    task.status = Task.Status.TODO if task.status == Task.Status.COMPLETED else Task.Status.COMPLETED
    task.save()
    return redirect(safe_next(request, reverse("planner:today")))


@login_required
@require_POST
def task_quick_add(request):
    form = QuickTaskForm(request.POST, user=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, "Task added.")
    else:
        messages.error(request, "Task not added: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect(safe_next(request, reverse("planner:today")))


# ===================== DAY / WEEK =====================
@login_required
def day_view(request):
    user = request.user
    today = local_today(user)
    day = parse_date(request.GET.get("date"), today)
    available = user.profile.daily_available_minutes
    tasks = list(
        Task.objects.filter(owner=user, due_date=day)
        .exclude(status=Task.Status.CANCELLED)
        .select_related("goal")
        .annotate(priority_rank=PRIORITY_RANK)
        .order_by(F("start_at").asc(nulls_last=True), "priority_rank", "id")
    )
    planned, unestimated = day_load(user, day)
    overdue = []
    if day == today:
        overdue = list(Task.objects.filter(owner=user, status__in=OPEN_STATUSES, due_date__lt=today).select_related("goal").order_by("due_date")[:20])
    ctx = {
        "page_title": "Today" if day == today else day.strftime("%A %-d %B"),
        "tab": "today", "day": day, "today": today, "tasks": tasks, "overdue": overdue,
        "prev_day": day - DAY, "next_day": day + DAY,
        "planned": planned, "available": available, "unestimated": unestimated,
        "over_by": max(0, planned - available),
        "load_pct": min(100, round(planned * 100 / available)) if available else 0,
        "quick_form": QuickTaskForm(user=user, initial={"due_date": day}),
        "next_url": request.get_full_path(),
    }
    return render(request, "planner/day.html", ctx)


@login_required
def week_view(request):
    user = request.user
    today = local_today(user)
    ws = user.profile.week_start
    anchor = parse_date(request.GET.get("start"), today)
    start = week_start_for(anchor, ws)
    plan = week_plan(user, start, user.profile.daily_available_minutes)
    ctx = {
        "page_title": "Week plan", "tab": "week", "plan": plan, "today": today, "start": start,
        "end": start + dt.timedelta(days=6), "prev_week": start - dt.timedelta(days=7),
        "next_week": start + dt.timedelta(days=7), "next_url": request.get_full_path(),
        "any_over": any(d["over_by"] for d in plan),
    }
    return render(request, "planner/week.html", ctx)


# ===================== HABITS =====================
def _logs_by_habit(user, habits):
    by = defaultdict(set)
    for hid, d in HabitLog.objects.filter(owner=user, habit__in=habits).values_list("habit_id", "date"):
        by[hid].add(d)
    return by


@login_required
def habit_tracker(request):
    user = request.user
    ensure_default_life_areas(user)
    today = local_today(user)
    wsd = user.profile.week_start
    anchor = parse_date(request.GET.get("week"), today)
    start, _ = hs.week_bounds(anchor, wsd)
    days = [start + dt.timedelta(days=i) for i in range(7)]
    habits = list(Habit.objects.filter(owner=user))
    logs = _logs_by_habit(user, habits)
    rows = []
    for h in habits:
        done = logs[h.id]
        stats = hs.habit_stats(h, done, today, wsd)
        win = hs.window(h, today)
        cells = []
        for d in days:
            cells.append({
                "date": d, "done": d in done, "scheduled": hs.scheduled_on(h, d),
                "future": d > today, "before_start": d < h.start_date,
                "can_toggle": h.is_active and d <= today and d >= h.start_date and (not h.end_date or d <= h.end_date),
            })
        rows.append({"habit": h, "cells": cells, "stats": stats})
    ctx = {
        "page_title": "Habits", "days": days, "rows": [r for r in rows if r["habit"].is_active],
        "paused": [r for r in rows if not r["habit"].is_active], "today": today, "start": start,
        "prev_week": start - dt.timedelta(days=7), "next_week": start + dt.timedelta(days=7),
        "next_url": request.get_full_path(),
    }
    return render(request, "planner/habit_tracker.html", ctx)


class HabitCreateView(OwnedCreateView):
    model = Habit
    form_class = HabitForm
    page_title = "New habit"
    success_message = "Habit created."
    cancel_url = reverse_lazy("planner:habits")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            ensure_default_life_areas(request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {**super().get_initial(), "start_date": local_today(self.request.user)}

    def get_default_success_url(self):
        return reverse("planner:habit_detail", args=[self.object.pk])


class HabitUpdateView(OwnedUpdateView):
    model = Habit
    form_class = HabitForm
    page_title = "Edit habit"
    success_message = "Habit updated. Your history is unchanged."

    def get_cancel_url(self):
        return reverse("planner:habit_detail", args=[self.object.pk])

    def get_default_success_url(self):
        return reverse("planner:habit_detail", args=[self.object.pk])


class HabitDeleteView(OwnedDeleteView):
    model = Habit
    noun = "habit"
    success_url = reverse_lazy("planner:habits")
    success_message = "Habit and its history deleted. Consider pausing a habit instead to keep its history."

    def get_cancel_url(self):
        return reverse("planner:habit_detail", args=[self.object.pk])


class HabitDetailView(OwnerQuerysetMixin, DetailView):
    model = Habit
    template_name = "planner/habit_detail.html"
    context_object_name = "habit"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user, habit = self.request.user, self.object
        today = local_today(user)
        wsd = user.profile.week_start
        logs = set(habit.logs.values_list("date", flat=True))
        # month calendar
        ym = self.request.GET.get("month", "")
        try:
            year, month = (int(x) for x in ym.split("-"))
            first = dt.date(year, month, 1)
        except (ValueError, TypeError):
            first = today.replace(day=1)
        cal = calendar.Calendar(firstweekday=wsd)
        weeks = []
        for week in cal.monthdatescalendar(first.year, first.month):
            weeks.append([{
                "date": d, "in_month": d.month == first.month, "done": d in logs,
                "scheduled": hs.scheduled_on(habit, d), "future": d > today,
                "can_toggle": habit.is_active and habit.start_date <= d <= today and (not habit.end_date or d <= habit.end_date),
            } for d in week])
        md, me = hs.monthly_consistency(habit, logs, first.year, first.month, today)
        prev_m = (first - DAY).replace(day=1)
        next_m = (first + dt.timedelta(days=32)).replace(day=1)
        header = [dt.date(2024, 1, 1) + dt.timedelta(days=(wsd + i) % 7) for i in range(7)]
        ctx.update(
            page_title=habit.name, stats=hs.habit_stats(habit, logs, today, wsd), weeks=weeks, month=first,
            month_done=md, month_expected=me, month_pct=hs.pct(md, me), prev_month=prev_m.strftime("%Y-%m"),
            next_month=next_m.strftime("%Y-%m"), weekday_header=header, today=today,
            recent_logs=habit.logs.all()[:10], next_url=self.request.get_full_path(),
        )
        return ctx


@login_required
@require_POST
def habit_log_toggle(request, pk):
    user = request.user
    habit = get_object_or_404(Habit, pk=pk, owner=user)
    today = local_today(user)
    day = parse_date(request.POST.get("date"), today)
    fallback = reverse("planner:habit_detail", args=[habit.pk])
    if day > today:
        messages.error(request, "You can't log a habit for a future date.")
    elif day < habit.start_date or (habit.end_date and day > habit.end_date):
        messages.error(request, "That date is outside this habit's active period.")
    elif not habit.is_active:
        messages.error(request, "This habit is paused. Resume it to log completions.")
    else:
        deleted, _ = HabitLog.objects.filter(habit=habit, owner=user, date=day).delete()
        if not deleted:
            try:
                with transaction.atomic():
                    HabitLog.objects.create(habit=habit, owner=user, date=day)
            except IntegrityError:
                pass  # a concurrent request already logged it; the unique constraint prevented a duplicate
    return redirect(safe_next(request, fallback))


@login_required
@require_POST
def habit_pause_toggle(request, pk):
    habit = get_object_or_404(Habit, pk=pk, owner=request.user)
    if habit.is_active:
        habit.is_active, habit.paused_on = False, local_today(request.user)
        messages.success(request, "Habit paused. All history is kept.")
    else:
        habit.is_active, habit.paused_on = True, None
        messages.success(request, "Habit resumed.")
    habit.save(update_fields=["is_active", "paused_on", "updated_at"])
    return redirect(safe_next(request, reverse("planner:habit_detail", args=[habit.pk])))
