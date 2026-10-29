from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.models import Priority
from apps.core.services import PRIORITY_RANK, local_today, parse_date
from apps.core.views import confirm_and_delete, safe_next
from apps.finance.calculations import affordability
from apps.finance.models import SavingsGoal
from apps.finance.services import goal_current_amount

from .forms import ActionForm, AffordabilityForm, FuturePlanForm
from .models import FuturePlan, PlanAction
from .services import annotate_plans, attach_info, average_monthly_net, plan_info

SORTS = {"target": [F("target_date").asc(nulls_last=True), "title"], "priority": ["priority_rank", F("target_date").asc(nulls_last=True)],
         "title": ["title"], "recent": ["-updated_at"]}


def _plan(request, pk):
    return get_object_or_404(FuturePlan.objects.select_related("savings_goal", "goal"), pk=pk, owner=request.user)


@login_required
def plan_list(request):
    user, g = request.user, request.GET
    qs = annotate_plans(FuturePlan.objects.filter(owner=user)).annotate(priority_rank=PRIORITY_RANK)
    show = g.get("show", "open")
    if show == "open":
        qs = qs.exclude(status__in=[FuturePlan.Status.ACHIEVED, FuturePlan.Status.CANCELLED])
    elif show == "achieved":
        qs = qs.filter(status=FuturePlan.Status.ACHIEVED)
    if g.get("category") in FuturePlan.Category.values:
        qs = qs.filter(category=g["category"])
    if g.get("priority") in Priority.values:
        qs = qs.filter(priority=g["priority"])
    if q := g.get("q", "").strip():
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
    qs = qs.order_by(*SORTS.get(g.get("sort"), SORTS["target"]))
    page = Paginator(qs, 12).get_page(g.get("page"))
    page.object_list = attach_info(page.object_list, local_today(user))
    params = g.copy(); params.pop("page", None)
    return render(request, "future_plans/plan_list.html", {"page_title": "Future Plans", "page_obj": page, "qs": params.urlencode(), "f": g, "show": show,
                                                           "categories": FuturePlan.Category.choices, "priorities": Priority.choices, "today": local_today(user)})


@login_required
def plan_form(request, pk=None):
    plan = _plan(request, pk) if pk else None
    form = FuturePlanForm(request.POST or None, instance=plan, user=request.user)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, "Plan saved.")
        return redirect("future_plans:detail", pk=saved.pk)
    return render(request, "components/form_page.html", {"form": form, "page_title": "Edit plan" if plan else "New plan",
                                                         "cancel_url": reverse("future_plans:detail", args=[plan.pk]) if plan else reverse("future_plans:list")})


@login_required
def plan_detail(request, pk):
    plan = _plan(request, pk)
    today = local_today(request.user)
    whatif = parse_date(request.GET.get("whatif"))
    saved = goal_current_amount(plan.savings_goal) if plan.savings_goal_id else None
    ctx = {"page_title": plan.title, "plan": plan, "today": today, "info": plan_info(plan, saved, today, whatif), "whatif_value": request.GET.get("whatif", ""),
           "actions": plan.actions.all(), "action_form": ActionForm(user=request.user, plan=plan),
           "interest_items": plan.interest_items.all(),
           "unlinked_goals_exist": SavingsGoal.objects.filter(owner=request.user, future_plan__isnull=True, currency=plan.currency).exists()}
    return render(request, "future_plans/plan_detail.html", ctx)


@login_required
def plan_delete(request, pk):
    plan = _plan(request, pk)
    extra = "Its action items are deleted too. A linked goal or savings goal is kept (only the link is removed)."
    return confirm_and_delete(request, plan, "plan", reverse("future_plans:detail", args=[plan.pk]), reverse("future_plans:list"), lambda p: p.delete(), "Plan deleted.", extra)


@login_required
@require_POST
def plan_create_savings(request, pk):
    """One-click: create a savings goal from the plan's cost and date, and link it."""
    plan = _plan(request, pk)
    if plan.savings_goal_id:
        messages.info(request, "This plan already has a savings goal.")
    elif not plan.estimated_cost:
        messages.error(request, "Add an estimated cost first, so the savings goal has a target.")
    else:
        with db_transaction.atomic():
            sg = SavingsGoal.objects.create(owner=request.user, name=plan.title, currency=plan.currency, target_amount=plan.estimated_cost,
                                            target_date=plan.target_date, priority=plan.priority)
            plan.savings_goal = sg
            plan.save(update_fields=["savings_goal", "updated_at"])
        messages.success(request, "Savings goal created and linked. You can change its target on the Finances page.")
    return redirect("future_plans:detail", pk=plan.pk)


@login_required
@require_POST
def action_add(request, pk):
    plan = _plan(request, pk)
    form = ActionForm(request.POST, user=request.user, plan=plan)
    if form.is_valid():
        form.instance.order = plan.actions.count()
        form.save()
        messages.success(request, "Action added.")
    else:
        messages.error(request, "Not added: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("future_plans:detail", pk=plan.pk)


@login_required
@require_POST
def action_toggle(request, pk):
    a = get_object_or_404(PlanAction, pk=pk, owner=request.user)
    a.is_done = not a.is_done
    a.save()
    return redirect(safe_next(request, reverse("future_plans:detail", args=[a.plan_id])))


@login_required
def action_delete(request, pk):
    a = get_object_or_404(PlanAction, pk=pk, owner=request.user)
    back = reverse("future_plans:detail", args=[a.plan_id])
    return confirm_and_delete(request, a, "action", back, back, lambda o: o.delete(), "Action deleted.")


@login_required
def affordability_view(request, pk=None):
    user = request.user
    today = local_today(user)
    plan = _plan(request, pk) if pk else None
    suggestion = average_monthly_net(user, user.profile.currency, today)
    initial = {}
    if plan:
        initial = {"cost": plan.estimated_cost, "target_date": plan.target_date,
                   "saved": goal_current_amount(plan.savings_goal) if plan.savings_goal_id else 0}
    if suggestion and suggestion > 0:
        initial["monthly_available"] = suggestion
    form = AffordabilityForm(request.GET or None, initial=initial)
    result = None
    if request.GET and form.is_valid():
        c = form.cleaned_data
        result = affordability(c["cost"], c["saved"] or 0, c["monthly_available"] or 0, c["target_date"], today)
        result["currency"] = plan.currency if plan else user.profile.currency
        result["inputs"] = c
    return render(request, "future_plans/affordability.html", {"page_title": "Affordability check", "form": form, "result": result, "plan": plan,
                                                               "suggestion": suggestion, "currency": plan.currency if plan else user.profile.currency})
