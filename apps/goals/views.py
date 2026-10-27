import datetime as dt

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Case, IntegerField, Prefetch, Q, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.views.generic import DetailView, ListView

from apps.core.services import PRIORITY_RANK, local_today
from apps.core.views import (
    OwnedCreateView, OwnedDeleteView, OwnedUpdateView, safe_next,
)
from apps.core.mixins import OwnerQuerysetMixin
from apps.core.models import Priority
from apps.planner.forms import QuickTaskForm
from apps.planner.models import Task

from .forms import GoalForm, LifeAreaForm, MilestoneForm
from .models import Goal, LifeArea, Milestone
from .services import annotate_progress, attach_progress, ensure_default_life_areas

SORTS = {
    "target": ("target_date", "title"),
    "priority": ("priority_rank", "target_date"),
    "title": ("title",),
    "recent": ("-updated_at",),
}


class GoalListView(LoginRequiredMixin, ListView):
    template_name = "goals/goal_list.html"
    paginate_by = 12

    def get_queryset(self):
        ensure_default_life_areas(self.request.user)
        g = self.request.GET
        today = local_today(self.request.user)
        qs = annotate_progress(Goal.objects.filter(owner=self.request.user).select_related("life_area"))
        qs = qs.annotate(priority_rank=PRIORITY_RANK)
        if q := g.get("q", "").strip():
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        if g.get("area", "").isdigit():
            qs = qs.filter(life_area_id=int(g["area"]))
        if g.get("priority") in Priority.values:
            qs = qs.filter(priority=g["priority"])
        if g.get("status") in Goal.Status.values:
            qs = qs.filter(status=g["status"])
        elif g.get("status") != "all":
            pass  # no status filter by default: show everything
        due = g.get("due")
        open_goals = ~Q(status__in=[Goal.Status.COMPLETED, Goal.Status.ABANDONED])
        if due == "overdue":
            qs = qs.filter(open_goals, target_date__lt=today)
        elif due == "30":
            qs = qs.filter(open_goals, target_date__gte=today, target_date__lte=today + dt.timedelta(days=30))
        elif due == "none":
            qs = qs.filter(target_date__isnull=True)
        order = SORTS.get(g.get("sort"), SORTS["target"])
        if order[0] == "target_date":
            from django.db.models import F
            return qs.order_by(F("target_date").asc(nulls_last=True), "title")
        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["goals"] = attach_progress(ctx["object_list"])
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx.update(
            page_title="My Goals",
            qs=params.urlencode(),
            layout="cards" if self.request.GET.get("layout") != "list" else "list",
            areas=LifeArea.objects.filter(owner=self.request.user),
            priorities=Priority.choices,
            statuses=Goal.Status.choices,
            f=self.request.GET,
            today=local_today(self.request.user),
        )
        return ctx


class GoalCreateView(OwnedCreateView):
    model = Goal
    form_class = GoalForm
    page_title = "New goal"
    success_message = "Goal created."
    cancel_url = reverse_lazy("goals:list")

    def get_default_success_url(self):
        return reverse("goals:detail", args=[self.object.pk])

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            ensure_default_life_areas(request.user)
        return super().dispatch(request, *args, **kwargs)


class GoalUpdateView(OwnedUpdateView):
    model = Goal
    form_class = GoalForm
    page_title = "Edit goal"
    success_message = "Goal updated."

    def get_cancel_url(self):
        return reverse("goals:detail", args=[self.object.pk])

    def get_default_success_url(self):
        return reverse("goals:detail", args=[self.object.pk])


class GoalDeleteView(OwnedDeleteView):
    model = Goal
    noun = "goal"
    success_url = reverse_lazy("goals:list")
    success_message = "Goal deleted. Its milestones were removed; linked tasks were kept."

    def get_cancel_url(self):
        return reverse("goals:detail", args=[self.object.pk])


class GoalDetailView(OwnerQuerysetMixin, DetailView):
    template_name = "goals/goal_detail.html"
    context_object_name = "goal"

    def get_queryset(self):
        # Must filter by owner explicitly: overriding get_queryset bypasses OwnerQuerysetMixin's filter.
        return annotate_progress(Goal.objects.filter(owner=self.request.user).select_related("life_area"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        goal = self.object
        user = self.request.user
        today = local_today(user)
        from .services import progress_from_annotations

        live = Task.objects.exclude(status=Task.Status.CANCELLED).order_by("due_date", "id")
        milestones = list(goal.milestones.prefetch_related(Prefetch("tasks", queryset=live)))
        loose_tasks = list(live.filter(goal=goal, milestone__isnull=True))
        open_ms = [m for m in milestones if m.status != Milestone.Status.COMPLETED]
        horizon = today + dt.timedelta(days=14)
        upcoming = sorted(
            [("Milestone", m.title, m.due_date) for m in open_ms if m.due_date and m.due_date <= horizon]
            + [("Task", t.title, t.due_date) for t in loose_tasks + [t for m in milestones for t in m.tasks.all()]
               if not t.is_done and t.due_date and t.due_date <= horizon],
            key=lambda x: x[2],
        )
        ctx.update(
            page_title=goal.title,
            progress=progress_from_annotations(goal),
            milestones=milestones,
            loose_tasks=loose_tasks,
            milestones_done=len(milestones) - len(open_ms),
            milestones_total=len(milestones),
            remaining_milestones=open_ms,
            upcoming=upcoming,
            today=today,
            milestone_form=MilestoneForm(user=user, goal=goal),
            task_form=QuickTaskForm(user=user, initial={"goal": goal.pk}),
        )
        return ctx


@login_required
@require_POST
def milestone_add(request, pk):
    goal = get_object_or_404(Goal, pk=pk, owner=request.user)
    form = MilestoneForm(request.POST, user=request.user, goal=goal)
    if form.is_valid():
        form.save()
        messages.success(request, "Milestone added.")
    else:
        messages.error(request, "Milestone not saved: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("goals:detail", pk=goal.pk)


class MilestoneUpdateView(OwnedUpdateView):
    model = Milestone
    form_class = MilestoneForm
    page_title = "Edit milestone"
    success_message = "Milestone updated."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["goal"] = self.object.goal
        return kwargs

    def get_cancel_url(self):
        return reverse("goals:detail", args=[self.object.goal_id])

    def get_default_success_url(self):
        return reverse("goals:detail", args=[self.object.goal_id])


class MilestoneDeleteView(OwnedDeleteView):
    model = Milestone
    noun = "milestone"
    success_message = "Milestone deleted. Its tasks were kept on the goal."

    def get_cancel_url(self):
        return reverse("goals:detail", args=[self.object.goal_id])

    def get_success_url(self):
        return safe_next(self.request, reverse("goals:detail", args=[self.object.goal_id]))


@login_required
@require_POST
def milestone_toggle(request, pk):
    ms = get_object_or_404(Milestone, pk=pk, owner=request.user)
    ms.status = Milestone.Status.TODO if ms.status == Milestone.Status.COMPLETED else Milestone.Status.COMPLETED
    ms.save()
    return redirect(safe_next(request, reverse("goals:detail", args=[ms.goal_id])))


@login_required
def life_areas(request):
    ensure_default_life_areas(request.user)
    form = LifeAreaForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Life area added.")
        return redirect("goals:areas")
    areas = LifeArea.objects.filter(owner=request.user)
    return render(request, "goals/life_areas.html", {"page_title": "Life areas", "form": form, "areas": areas})


@login_required
@require_POST
def life_area_delete(request, pk):
    area = get_object_or_404(LifeArea, pk=pk, owner=request.user)
    area.delete()
    messages.success(request, "Life area removed. Goals that used it are now uncategorised.")
    return redirect("goals:areas")
