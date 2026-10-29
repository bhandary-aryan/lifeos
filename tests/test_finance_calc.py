import datetime as dt
from decimal import Decimal as D

import pytest

from apps.finance.calculations import (
    change_pct, contribution_plan, month_bounds, months_remaining, percent, q2, shift_month, simulate_repayment,
)

d = dt.date


# ---------- month boundaries ----------
@pytest.mark.parametrize("y,m,first,nxt", [
    (2026, 1, d(2026, 1, 1), d(2026, 2, 1)),
    (2026, 2, d(2026, 2, 1), d(2026, 3, 1)),
    (2028, 2, d(2028, 2, 1), d(2028, 3, 1)),      # leap year
    (2026, 12, d(2026, 12, 1), d(2027, 1, 1)),    # year rollover
])
def test_month_bounds(y, m, first, nxt):
    assert month_bounds(y, m) == (first, nxt)


@pytest.mark.parametrize("y,m,delta,expected", [(2026, 1, -1, (2025, 12)), (2026, 12, 1, (2027, 1)), (2026, 3, -14, (2025, 1)), (2026, 6, 0, (2026, 6))])
def test_shift_month(y, m, delta, expected):
    assert shift_month(y, m, delta) == expected


# ---------- months remaining ----------
@pytest.mark.parametrize("today,target,expected", [
    (d(2026, 1, 15), d(2026, 1, 15), 1),     # today
    (d(2026, 1, 15), d(2026, 1, 31), 1),     # same month, later
    (d(2026, 1, 15), d(2026, 2, 15), 1),     # exactly one month
    (d(2026, 1, 15), d(2026, 2, 16), 2),     # one month and a day -> partial counts as a month
    (d(2026, 1, 31), d(2026, 3, 1), 2),
    (d(2026, 1, 15), d(2027, 1, 15), 12),
    (d(2026, 12, 20), d(2027, 1, 5), 1),     # across year end
    (d(2026, 1, 15), d(2026, 1, 14), 0),     # past
])
def test_months_remaining(today, target, expected):
    assert months_remaining(today, target) == expected


# ---------- contribution plan ----------
TODAY = d(2026, 1, 15)


def test_plan_basic_division():
    p = contribution_plan("1200", "0", d(2027, 1, 15), TODAY)
    assert (p["state"], p["months"], p["monthly"], p["remaining"]) == ("ok", 12, D("100.00"), D("1200.00"))


def test_plan_rounds_up_so_target_is_actually_reached():
    p = contribution_plan("100", "0", d(2026, 4, 15), TODAY)       # 100 / 3 = 33.333...
    assert p["monthly"] == D("33.34") and p["monthly"] * 3 >= 100


def test_plan_uses_remaining_not_total():
    p = contribution_plan("1000", "400", d(2026, 7, 15), TODAY)
    assert p["remaining"] == D("600.00") and p["months"] == 6 and p["monthly"] == D("100.00")


@pytest.mark.parametrize("current", ["1000", "1500.50"])
def test_plan_already_achieved_even_if_overshot(current):
    p = contribution_plan("1000", current, d(2026, 6, 1), TODAY)
    assert p["state"] == "achieved" and p["monthly"] == D("0.00") and p["remaining"] == D("0.00")


def test_plan_achieved_beats_overdue():
    assert contribution_plan("100", "100", d(2020, 1, 1), TODAY)["state"] == "achieved"


def test_plan_overdue_never_presented_as_achievable():
    p = contribution_plan("1000", "100", d(2025, 12, 31), TODAY)
    assert p["state"] == "overdue" and p["monthly"] is None


def test_plan_without_target_date():
    p = contribution_plan("1000", "0", None, TODAY)
    assert p["state"] == "no_date" and p["monthly"] is None


def test_plan_target_date_same_day_means_one_month_bucket():
    assert contribution_plan("300", "0", TODAY, TODAY)["monthly"] == D("300.00")


def test_plan_recalculates_when_target_date_changes():
    near = contribution_plan("1200", "0", d(2026, 7, 15), TODAY)["monthly"]
    far = contribution_plan("1200", "0", d(2027, 1, 15), TODAY)["monthly"]
    assert near == D("200.00") and far == D("100.00")


def test_percent_and_change_pct():
    assert percent(0, 0) == 0 and percent(1, 3) == 33 and percent(5, 4) == 100
    assert change_pct(150, 100) == D("50.0") and change_pct(50, 100) == D("-50.0") and change_pct(10, 0) is None


def test_q2_rounds_half_up():
    assert q2("1.005") == D("1.01") and q2("2.004") == D("2.00")


# ---------- repayment simulation ----------
def debts():
    return [
        {"id": 1, "name": "Card", "balance": "1000", "rate": "24", "minimum": "30"},
        {"id": 2, "name": "Small loan", "balance": "300", "rate": "5", "minimum": "20"},
    ]


def test_avalanche_pays_highest_rate_first_and_costs_less_interest():
    av = simulate_repayment(debts(), "200", "avalanche")
    sn = simulate_repayment(debts(), "200", "snowball")
    assert av["paid_off"] and sn["paid_off"]
    assert av["total_interest"] <= sn["total_interest"]
    assert sn["order"][0]["name"] == "Small loan"            # snowball clears the smallest balance first


def test_strategies_differ_when_small_balance_has_low_rate():
    av = simulate_repayment(debts(), "100", "avalanche")
    sn = simulate_repayment(debts(), "100", "snowball")
    assert av["total_interest"] < sn["total_interest"]


def test_zero_rate_debt_pays_down_linearly():
    r = simulate_repayment([{"id": 1, "name": "Friend", "balance": "500", "rate": "0", "minimum": "100"}], "100", "avalanche")
    assert r["months"] == 5 and r["total_interest"] == D("0.00") and r["total_paid"] == D("500.00")


def test_budget_below_minimums_is_flagged_not_simulated():
    r = simulate_repayment(debts(), "40", "avalanche")
    assert r["insufficient_budget"] and r["months"] == 0 and not r["paid_off"]


def test_payment_that_never_beats_interest_is_not_reported_as_paid_off():
    r = simulate_repayment([{"id": 1, "name": "Trap", "balance": "10000", "rate": "30", "minimum": "100"}], "100", "avalanche", max_months=24)
    assert not r["paid_off"] and r["months"] == 24


def test_no_debts_is_trivially_paid_off():
    assert simulate_repayment([], "100", "snowball")["paid_off"] is True
    assert simulate_repayment([{"id": 1, "name": "x", "balance": "0", "rate": "5", "minimum": "5"}], "10", "snowball")["paid_off"] is True


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        simulate_repayment(debts(), "100", "yolo")


def test_simulation_totals_reconcile():
    r = simulate_repayment(debts(), "150", "avalanche")
    assert r["total_paid"] == D("1300.00") + r["total_interest"]       # principal + interest, nothing lost or invented


# ---------- add_months / affordability ----------
from apps.finance.calculations import add_months, affordability  # noqa: E402


@pytest.mark.parametrize("start,n,expected", [
    (d(2026, 1, 31), 1, d(2026, 2, 28)), (d(2028, 1, 31), 1, d(2028, 2, 29)), (d(2026, 12, 15), 1, d(2027, 1, 15)),
    (d(2026, 3, 31), -1, d(2026, 2, 28)), (d(2026, 5, 10), 0, d(2026, 5, 10)), (d(2026, 1, 15), 25, d(2028, 2, 15)),
])
def test_add_months_clamps_day(start, n, expected):
    assert add_months(start, n) == expected


def aff(cost="12000", saved="2000", avail="500", target=d(2027, 1, 15), today=d(2026, 1, 15)):
    return affordability(cost, saved, avail, target, today)


def test_affordability_on_track_when_available_covers_required():
    r = aff()                                     # remaining 10000 over 12 months = 833.34/mo needed
    assert r["required_monthly"] == D("833.34") and r["state"] == "shortfall" and r["shortfall_monthly"] == D("333.34")
    ok = aff(avail="900")
    assert ok["state"] == "on_track" and ok["shortfall_monthly"] is None


def test_affordability_projects_completion_date_at_available_rate():
    r = aff(avail="500")
    assert r["months_at_available"] == 20 and r["projected_date"] == d(2027, 9, 15)       # ceil(10000/500)=20 months


def test_affordability_projection_rounds_up():
    assert aff(cost="1001", saved="0", avail="500")["months_at_available"] == 3


def test_affordability_achieved_and_no_capacity_and_overdue():
    assert aff(saved="12000")["state"] == "achieved" and aff(saved="99999")["remaining"] == D("0.00")
    nc = aff(avail="0")
    assert nc["state"] == "no_capacity" and nc["projected_date"] is None and nc["months_at_available"] is None
    assert aff(avail="-50")["state"] == "no_capacity"
    late = aff(target=d(2025, 12, 1))
    assert late["state"] == "overdue_shortfall" and late["required_monthly"] is None


def test_affordability_without_deadline_only_projects():
    r = aff(target=None, avail="1000")
    assert r["state"] == "on_track" and r["required_monthly"] is None and r["months_at_available"] == 10


def test_affordability_never_says_affordable_without_inputs():
    assert aff(avail=None)["state"] == "no_capacity" and aff(avail="")["state"] == "no_capacity"
