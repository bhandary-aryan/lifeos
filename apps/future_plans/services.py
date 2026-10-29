import datetime as dt

from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from apps.finance.calculations import ZERO, contribution_plan, percent, shift_month
from apps.finance.models import SavingsContribution
from apps.finance.services import month_summary

from .models import FuturePlan

MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


def plan_info(plan, saved, today, whatif_date=None):
    """Everything the UI shows for a plan, with the money figures kept apart.

    estimated_cost  the user's guess of the total cost
    savings_target  the linked savings goal's target (may differ from the cost)
    saved           REAL contributions recorded on the linked goal (None if no goal is linked)
    The monthly estimate is remaining / remaining months (see calculations.contribution_plan).
    """
    sg = plan.savings_goal if plan.savings_goal_id else None
    target_date = whatif_date or plan.target_date
    cost = plan.estimated_cost
    info = {"estimated_cost": cost, "savings_target": sg.target_amount if sg else None, "saved": saved if sg else None,
            "target_date": target_date, "whatif": bool(whatif_date), "basis": None, "estimate": None,
            "saving_pct": percent(saved, sg.target_amount) if sg else None,
            "coverage_pct": percent(sg.target_amount, cost) if sg and cost else None,
            "gap_to_cost": max(cost - sg.target_amount, ZERO) if sg and cost else None,
            "needs_update": bool(plan.target_date and plan.target_date < today and plan.is_open)}
    if sg:
        info["basis"] = "savings_goal"
        info["estimate"] = contribution_plan(sg.target_amount, saved, target_date, today)
    elif cost:
        info["basis"] = "cost_only"            # nothing saved is assumed; clearly labelled in the UI
        info["estimate"] = contribution_plan(cost, ZERO, target_date, today)
    return info


def _saved_subquery():
    return Coalesce(Subquery(SavingsContribution.objects.filter(goal=OuterRef("savings_goal_id")).order_by().values("goal")
                             .annotate(s=Sum("amount")).values("s"), output_field=MONEY_FIELD), Value(ZERO))


def annotate_plans(qs):
    return qs.select_related("savings_goal", "goal").annotate(saved_amount=_saved_subquery())


def attach_info(plans, today):
    plans = list(plans)
    for p in plans:
        p.info = plan_info(p, p.saved_amount, today)
    return plans


def plans_with_info(user, today, qs=None):
    return attach_info(annotate_plans(qs if qs is not None else FuturePlan.objects.filter(owner=user)), today)


def average_monthly_net(user, currency, today, months=3):
    """Average net cash flow over the last `months` FULL calendar months, or None if there is no activity.

    Only a suggested starting value for the affordability page; the user always chooses their own figure.
    """
    from apps.finance.models import Transaction

    vals, any_activity = [], False
    for i in range(1, months + 1):
        y, m = shift_month(today.year, today.month, -i)
        s = month_summary(user, currency, y, m)
        vals.append(s["net_cash_flow"])
        any_activity = any_activity or bool(s["income"] or s["spending"] or s["debt_paid"])
    if not any_activity:
        return None
    return (sum(vals, ZERO) / months).quantize(ZERO)


def approaching_plans(user, today, days=90):
    horizon = today + dt.timedelta(days=days)
    qs = FuturePlan.objects.filter(owner=user, target_date__isnull=False, target_date__lte=horizon,
                                   status__in=[FuturePlan.Status.PLANNING, FuturePlan.Status.IN_PROGRESS, FuturePlan.Status.IDEA]).order_by("target_date")
    return plans_with_info(user, today, qs)[:5]
