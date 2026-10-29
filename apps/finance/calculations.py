"""Pure, deterministic finance maths (no database, no clocks).

Everything here is an ESTIMATE helper: no investment returns, no inflation,
no changing interest rates. Results must be labelled as estimates in the UI.
"""
import calendar
import datetime as dt
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def q2(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def month_bounds(year: int, month: int):
    """[first day of month, first day of next month): exact boundaries incl. Dec -> Jan."""
    first = dt.date(year, month, 1)
    nxt = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return first, nxt


def shift_month(year: int, month: int, delta: int):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def months_remaining(today: dt.date, target: dt.date) -> int:
    """Whole months left until `target`, counting a partial month as a full one.

    Same-month future dates (and today itself) give 1. Past dates give 0.
    """
    if target < today:
        return 0
    months = (target.year - today.year) * 12 + (target.month - today.month)
    if target.day > today.day:
        months += 1
    return max(months, 1)


def contribution_plan(target_amount, current_amount, target_date, today):
    """Basic contribution estimate: remaining / remaining months (rounded UP to the cent).

    Returns a dict with state in {"achieved", "no_date", "overdue", "ok"}.
    Never divides by zero and never presents a past date as achievable.
    """
    target, current = Decimal(target_amount), Decimal(current_amount)
    remaining = max(target - current, ZERO)
    out = {"remaining": q2(remaining), "monthly": None, "months": None, "state": "ok"}
    if remaining <= 0:
        out.update(state="achieved", monthly=ZERO, months=0)
        return out
    if target_date is None:
        out["state"] = "no_date"
        return out
    months = months_remaining(today, target_date)
    if months == 0:
        out["state"] = "overdue"
        return out
    out["months"] = months
    out["monthly"] = (remaining / months).quantize(CENT, rounding=ROUND_CEILING)
    return out


def percent(done, total) -> int:
    done, total = Decimal(done), Decimal(total)
    if total <= 0:
        return 0
    return max(0, min(100, int((done * 100 / total).quantize(Decimal("1"), rounding=ROUND_HALF_UP))))


def change_pct(current, previous):
    """Percentage change vs a previous period; None when the previous value is zero."""
    previous = Decimal(previous)
    if previous == 0:
        return None
    return ((Decimal(current) - previous) * 100 / previous).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def simulate_repayment(debts, monthly_budget, strategy, max_months=600):
    """Month-by-month payoff estimate for several debts.

    debts: iterable of dicts {id, name, balance, rate (annual %), minimum}
    strategy: "avalanche" (highest rate first) or "snowball" (smallest balance first)
    Assumes interest = balance * rate / 12 added monthly, minimums paid on every
    debt, and all remaining budget going to the strategy's target debt.
    """
    if strategy not in ("avalanche", "snowball"):
        raise ValueError("strategy must be 'avalanche' or 'snowball'")
    monthly_budget = Decimal(monthly_budget)
    state = [
        {"id": d["id"], "name": d["name"], "balance": q2(d["balance"]), "rate": Decimal(d["rate"]),
         "minimum": q2(d["minimum"]), "paid_off_month": None}
        for d in debts if Decimal(d["balance"]) > 0
    ]
    min_total = sum((d["minimum"] for d in state), ZERO)
    result = {"strategy": strategy, "months": 0, "total_interest": ZERO, "total_paid": ZERO,
              "paid_off": not state, "insufficient_budget": monthly_budget < min_total,
              "minimum_total": min_total, "order": []}
    if not state or result["insufficient_budget"]:
        return result

    def priority(d):
        return (-d["rate"], d["balance"]) if strategy == "avalanche" else (d["balance"], -d["rate"])

    month = 0
    while month < max_months and any(d["balance"] > 0 for d in state):
        month += 1
        for d in state:
            if d["balance"] > 0:
                interest = q2(d["balance"] * d["rate"] / 100 / 12)
                d["balance"] += interest
                result["total_interest"] += interest
        budget = monthly_budget
        for d in state:                                   # minimums first
            if d["balance"] > 0:
                pay = min(d["minimum"], d["balance"])
                d["balance"] -= pay; budget -= pay; result["total_paid"] += pay
        for d in sorted((x for x in state if x["balance"] > 0), key=priority):   # extra to target debt
            if budget <= 0:
                break
            pay = min(budget, d["balance"])
            d["balance"] -= pay; budget -= pay; result["total_paid"] += pay
        for d in state:
            if d["balance"] <= 0 and d["paid_off_month"] is None:
                d["paid_off_month"] = month
                result["order"].append({"name": d["name"], "month": month})
    result["months"] = month
    result["paid_off"] = all(d["balance"] <= 0 for d in state)
    result["total_interest"] = q2(result["total_interest"])
    result["total_paid"] = q2(result["total_paid"])
    return result


def add_months(day: dt.date, months: int) -> dt.date:
    """`day` shifted by whole months; the day is clamped to the target month's length."""
    y, m = shift_month(day.year, day.month, months)
    return dt.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def affordability(cost, saved, monthly_available, target_date, today):
    """Deterministic 'can I reach this by then?' estimate. Never a promise.

    Assumes no returns, inflation or price changes, and a constant monthly saving.
    state: achieved | no_capacity | on_track | shortfall | overdue_shortfall
    """
    cost, saved, avail = Decimal(cost), Decimal(saved or 0), Decimal(monthly_available or 0)
    remaining = max(cost - saved, ZERO)
    out = {"remaining": q2(remaining), "required_monthly": None, "months_to_target": None, "months_at_available": None,
           "projected_date": None, "shortfall_monthly": None, "state": "on_track"}
    if remaining <= 0:
        out.update(state="achieved", required_monthly=ZERO, shortfall_monthly=ZERO)
        return out
    if avail > 0:
        n = int((remaining / avail).to_integral_value(rounding=ROUND_CEILING))
        out["months_at_available"] = n
        out["projected_date"] = add_months(today, n)
    plan = contribution_plan(cost, saved, target_date, today) if target_date else None
    if plan:
        out["months_to_target"] = plan["months"]
        out["required_monthly"] = plan["monthly"]
    if avail <= 0:
        out["state"] = "no_capacity"
    elif plan is None:
        out["state"] = "on_track"            # no deadline: only the projected date is meaningful
    elif plan["state"] == "overdue":
        out["state"] = "overdue_shortfall"
    elif avail >= plan["monthly"]:
        out["state"] = "on_track"
    else:
        out["state"] = "shortfall"
        out["shortfall_monthly"] = q2(plan["monthly"] - avail)
    return out
