import datetime as dt
import json
from decimal import Decimal as D

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.core.services import local_today
from apps.finance import services as fin
from apps.finance.models import SavingsContribution, SavingsGoal, Transaction
from apps.future_plans.models import FuturePlan, PlanAction
from apps.future_plans.services import approaching_plans, average_monthly_net, plan_info, plans_with_info

pytestmark = pytest.mark.django_db
T = Transaction.Type
DAY = dt.timedelta(days=1)


@pytest.fixture
def make_plan(db):
    def _make(owner, **kw):
        kw.setdefault("title", "Plan")
        return FuturePlan.objects.create(owner=owner, **kw)
    return _make


def plan_data(**over):
    d = {"title": "Buy a flat", "category": "house", "priority": "high", "status": "planning", "currency": "GBP"}
    d.update(over)
    return d


def contribute(user, goal, amount):
    SavingsContribution.objects.create(owner=user, goal=goal, date=local_today(user), amount=D(str(amount)))


# ---------------------------------------------------------------- model
def test_cost_must_be_positive_in_form_and_database(user, auth_client, make_plan):
    r = auth_client.post(reverse("future_plans:create"), plan_data(estimated_cost="0"))
    assert r.status_code == 200 and not FuturePlan.objects.exists()
    with pytest.raises(IntegrityError), transaction.atomic():
        FuturePlan.objects.create(owner=user, title="x", estimated_cost=D("-5"))
    assert make_plan(user, estimated_cost=None).pk


def test_achieved_date_set_and_cleared(user, make_plan):
    p = make_plan(user)
    assert p.achieved_on is None
    p.status = "achieved"; p.save()
    assert p.achieved_on is not None
    p.status = "in_progress"; p.save()
    assert p.achieved_on is None


def test_linked_savings_goal_currency_must_match(user, make_plan, make_savings):
    sg = make_savings(user, currency="USD")
    from django.core.exceptions import ValidationError
    with pytest.raises(ValidationError, match="never converted"):
        FuturePlan(owner=user, title="x", currency="GBP", savings_goal=sg).full_clean()


def test_a_savings_goal_links_to_at_most_one_plan(user, make_plan, make_savings, auth_client):
    sg = make_savings(user)
    make_plan(user, title="First", savings_goal=sg)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_plan(user, title="Second", savings_goal=sg)
    r = auth_client.get(reverse("future_plans:create"))
    assert f'value="{sg.pk}"' not in r.content.decode()          # already-linked goals are not offered


def test_deleting_linked_records_only_unlinks(user, make_plan, make_savings, make_goal):
    sg, g = make_savings(user), make_goal(user)
    p = make_plan(user, savings_goal=sg, goal=g)
    sg.delete(); g.delete()
    p.refresh_from_db()
    assert p.savings_goal is None and p.goal is None and FuturePlan.objects.filter(pk=p.pk).exists()


def test_deleting_plan_keeps_savings_goal_and_removes_actions(user, make_plan, make_savings):
    sg = make_savings(user); p = make_plan(user, savings_goal=sg)
    PlanAction.objects.create(owner=user, plan=p, title="a")
    p.delete()
    assert SavingsGoal.objects.filter(pk=sg.pk).exists() and not PlanAction.objects.exists()


# --------------------------------------------------------- calculations
def test_figures_stay_separate_and_derive_from_real_data(user, make_plan, make_savings):
    t = local_today(user)
    sg = make_savings(user, target="8000")
    p = make_plan(user, estimated_cost=D("10000"), savings_goal=sg, target_date=t + dt.timedelta(days=365))
    contribute(user, sg, 2000)
    info = plan_info(p, fin.goal_current_amount(sg), t)
    assert (info["estimated_cost"], info["savings_target"], info["saved"]) == (D("10000.00"), D("8000.00"), D("2000.00"))
    assert info["saving_pct"] == 25 and info["coverage_pct"] == 80 and info["gap_to_cost"] == D("2000.00")
    assert info["basis"] == "savings_goal" and info["estimate"]["remaining"] == D("6000.00")      # based on the savings target, not the cost


def test_monthly_estimate_matches_remaining_over_months(user, make_plan, make_savings):
    t = dt.date(2026, 1, 15)
    sg = make_savings(user, target="1200")
    p = make_plan(user, savings_goal=sg, target_date=dt.date(2027, 1, 15))
    e = plan_info(p, D("0"), t)["estimate"]
    assert (e["months"], e["monthly"], e["state"]) == (12, D("100.00"), "ok")


def test_cost_only_plan_assumes_nothing_saved_and_says_so(user, make_plan):
    t = dt.date(2026, 1, 15)
    p = make_plan(user, estimated_cost=D("600"), target_date=dt.date(2026, 7, 15))
    info = plan_info(p, None, t)
    assert info["basis"] == "cost_only" and info["saved"] is None and info["estimate"]["monthly"] == D("100.00")


def test_plan_without_cost_or_goal_has_no_estimate(user, make_plan):
    assert plan_info(make_plan(user), None, local_today(user))["estimate"] is None


def test_changing_target_date_recalculates_immediately(user, make_plan, make_savings):
    t = dt.date(2026, 1, 15)
    sg = make_savings(user, target="1200")
    p = make_plan(user, savings_goal=sg, target_date=dt.date(2026, 7, 15))
    assert plan_info(p, D("0"), t)["estimate"]["monthly"] == D("200.00")
    p.target_date = dt.date(2027, 1, 15); p.save()
    assert plan_info(p, D("0"), t)["estimate"]["monthly"] == D("100.00")
    whatif = plan_info(p, D("0"), t, whatif_date=dt.date(2026, 4, 15))
    assert whatif["whatif"] and whatif["estimate"]["monthly"] == D("400.00") and p.target_date == dt.date(2027, 1, 15)   # nothing saved


def test_achieved_overdue_and_no_date_states(user, make_plan, make_savings):
    t = dt.date(2026, 6, 1)
    sg = make_savings(user, target="100")
    done = plan_info(make_plan(user, savings_goal=sg, target_date=dt.date(2026, 12, 1)), D("150"), t)
    assert done["estimate"]["state"] == "achieved" and done["estimate"]["monthly"] == D("0.00")
    sg2 = make_savings(user, "Other", target="100")
    late = plan_info(make_plan(user, title="Late", savings_goal=sg2, target_date=dt.date(2026, 5, 1)), D("10"), t)
    assert late["estimate"]["state"] == "overdue" and late["estimate"]["monthly"] is None and late["needs_update"] is True
    nodate = plan_info(make_plan(user, title="Nodate", estimated_cost=D("50")), None, t)
    assert nodate["estimate"]["state"] == "no_date" and nodate["needs_update"] is False


def test_achieved_or_cancelled_past_plans_do_not_need_update(user, make_plan):
    t = dt.date(2026, 6, 1)
    for status in ("achieved", "cancelled"):
        p = make_plan(user, title=status, status=status, target_date=dt.date(2026, 1, 1))
        assert plan_info(p, None, t)["needs_update"] is False


def test_plans_with_info_matches_single_plan_info_without_n_plus_one(user, make_plan, make_savings, django_assert_max_num_queries):
    t = local_today(user)
    for i in range(6):
        sg = make_savings(user, f"G{i}", target="500")
        make_plan(user, title=f"P{i}", savings_goal=sg, target_date=t + dt.timedelta(days=100))
        contribute(user, sg, 50 + i)
    with django_assert_max_num_queries(3):
        plans = plans_with_info(user, t)
    assert sorted(p.info["saved"] for p in plans) == [D("50.00"), D("51.00"), D("52.00"), D("53.00"), D("54.00"), D("55.00")]


# ------------------------------------------------------------ views: CRUD
def test_create_edit_delete_plan_flow(auth_client, user):
    r = auth_client.post(reverse("future_plans:create"), plan_data(estimated_cost="25000", target_date="2030-01-01"))
    p = FuturePlan.objects.get(owner=user)
    assert r.status_code == 302 and r.url == reverse("future_plans:detail", args=[p.pk]) and p.estimated_cost == D("25000.00")
    auth_client.post(reverse("future_plans:update", args=[p.pk]), plan_data(title="Buy a house", estimated_cost="30000"))
    p.refresh_from_db(); assert (p.title, p.estimated_cost) == ("Buy a house", D("30000.00"))
    assert auth_client.get(reverse("future_plans:delete", args=[p.pk])).status_code == 200 and FuturePlan.objects.exists()
    auth_client.post(reverse("future_plans:delete", args=[p.pk]))
    assert not FuturePlan.objects.exists()


def test_invalid_plan_form_shows_errors(auth_client):
    r = auth_client.post(reverse("future_plans:create"), plan_data(title="", estimated_cost="-1", currency="XXX"))
    assert r.status_code == 200 and not FuturePlan.objects.exists()


def test_empty_states(auth_client):
    assert "No plans yet" in auth_client.get(reverse("future_plans:list")).content.decode()


def test_list_filters_search_sort_and_pagination(auth_client, user, make_plan):
    t = local_today(user)
    make_plan(user, title="Wedding fund", category="wedding", priority="low", target_date=t + dt.timedelta(days=400))
    make_plan(user, title="New car", category="vehicle", priority="high", target_date=t + dt.timedelta(days=100))
    make_plan(user, title="Old trip", category="travel", status="achieved")
    url = reverse("future_plans:list")
    def seen(**q):
        h = auth_client.get(url, q).content.decode()
        return {n for n in ("Wedding fund", "New car", "Old trip") if n in h}
    assert seen() == {"Wedding fund", "New car"}                                 # open plans by default
    assert seen(show="achieved") == {"Old trip"} and seen(show="all") == {"Wedding fund", "New car", "Old trip"}
    assert seen(category="vehicle") == {"New car"} and seen(priority="low") == {"Wedding fund"} and seen(q="wedding") == {"Wedding fund"}
    h = auth_client.get(url, {"sort": "priority"}).content.decode()
    assert h.index("New car") < h.index("Wedding fund")
    h = auth_client.get(url).content.decode()
    assert h.index("New car") < h.index("Wedding fund")                          # default: soonest target date first
    for i in range(13):
        make_plan(user, title=f"Bulk {i:02d}")
    assert "Page 1 of 2" in auth_client.get(url).content.decode()


def test_detail_shows_three_separate_figures_and_labels_estimates(auth_client, user, make_plan, make_savings):
    t = local_today(user)
    sg = make_savings(user, "Flat fund", target="8000")
    p = make_plan(user, estimated_cost=D("10000"), savings_goal=sg, target_date=t + dt.timedelta(days=365))
    contribute(user, sg, 2000)
    h = auth_client.get(reverse("future_plans:detail", args=[p.pk])).content.decode()
    for text in ("£10,000.00", "£8,000.00", "£2,000.00", "Estimated total cost", "Actually saved", "covers 80%", "This is an estimate, not a promise", "Flat fund"):
        assert text in h, text


def test_detail_whatif_date_recalculates_without_saving(auth_client, user, make_plan, make_savings):
    t = local_today(user)
    sg = make_savings(user, target="1200")
    p = make_plan(user, savings_goal=sg, target_date=t + dt.timedelta(days=365))
    far = auth_client.get(reverse("future_plans:detail", args=[p.pk])).content.decode()
    near = auth_client.get(reverse("future_plans:detail", args=[p.pk]), {"whatif": (t + dt.timedelta(days=62)).isoformat()}).content.decode()
    assert "what-if date" in near and "what-if date" not in far and near != far
    p.refresh_from_db(); assert p.target_date == t + dt.timedelta(days=365)
    assert auth_client.get(reverse("future_plans:detail", args=[p.pk]), {"whatif": "garbage"}).status_code == 200
    past = auth_client.get(reverse("future_plans:detail", args=[p.pk]), {"whatif": (t - DAY).isoformat()}).content.decode()
    assert "already passed" in past


def test_detail_warns_when_target_date_passed(auth_client, user, make_plan):
    p = make_plan(user, target_date=local_today(user) - DAY, estimated_cost=D("100"))
    h = auth_client.get(reverse("future_plans:detail", args=[p.pk])).content.decode()
    assert "target date has passed" in h and "Target date passed" in auth_client.get(reverse("future_plans:list")).content.decode()


# ----------------------------------------------------- savings goal links
def test_create_linked_savings_goal_from_plan(auth_client, user, make_plan):
    t = local_today(user)
    p = make_plan(user, title="Dream trip", estimated_cost=D("3000"), target_date=t + dt.timedelta(days=200), priority="high")
    auth_client.post(reverse("future_plans:create_savings", args=[p.pk]))
    p.refresh_from_db()
    sg = SavingsGoal.objects.get(owner=user)
    assert p.savings_goal == sg and (sg.name, sg.target_amount, sg.target_date, sg.currency, sg.priority) == ("Dream trip", D("3000.00"), p.target_date, "GBP", "high")
    auth_client.post(reverse("future_plans:create_savings", args=[p.pk]))                  # second click: no duplicate
    assert SavingsGoal.objects.count() == 1


def test_create_savings_goal_requires_a_cost(auth_client, user, make_plan):
    p = make_plan(user)
    r = auth_client.post(reverse("future_plans:create_savings", args=[p.pk]), follow=True)
    assert not SavingsGoal.objects.exists() and "Add an estimated cost first" in r.content.decode()


def test_create_savings_requires_post(auth_client, user, make_plan):
    assert auth_client.get(reverse("future_plans:create_savings", args=[make_plan(user, estimated_cost=D("5")).pk])).status_code == 405


def test_linking_existing_goal_through_form_and_progress_follows_contributions(auth_client, user, make_plan, make_savings):
    sg = make_savings(user, target="1000")
    auth_client.post(reverse("future_plans:create"), plan_data(estimated_cost="1000", savings_goal=sg.pk))
    p = FuturePlan.objects.get(owner=user)
    assert p.savings_goal == sg and sg.future_plan == p
    contribute(user, sg, 250)
    assert "25%" in auth_client.get(reverse("future_plans:detail", args=[p.pk])).content.decode()
    detail = auth_client.get(reverse("finance:savings_detail", args=[sg.pk])).content.decode()
    assert "Part of the future plan" in detail and reverse("future_plans:detail", args=[p.pk]) in detail
    assert "future plan linked to it is kept" in auth_client.get(reverse("finance:savings_delete", args=[sg.pk])).content.decode()
    plain = make_savings(user, "Plain")
    assert "Part of the future plan" not in auth_client.get(reverse("finance:savings_detail", args=[plain.pk])).content.decode()


def test_form_rejects_currency_mismatch(auth_client, user, make_savings):
    sg = make_savings(user, currency="USD")
    r = auth_client.post(reverse("future_plans:create"), plan_data(savings_goal=sg.pk, currency="GBP"))
    assert r.status_code == 200 and "never converted" in r.content.decode() and not FuturePlan.objects.exists()


# ---------------------------------------------------------------- actions
def test_action_items_add_toggle_delete(auth_client, user, make_plan):
    p = make_plan(user)
    auth_client.post(reverse("future_plans:action_add", args=[p.pk]), {"title": "Get a mortgage quote", "due_date": "2026-09-01"})
    a = PlanAction.objects.get()
    assert (a.title, a.order, a.is_done) == ("Get a mortgage quote", 0, False)
    auth_client.post(reverse("future_plans:action_toggle", args=[a.pk]))
    a.refresh_from_db(); assert a.is_done and a.completed_at
    auth_client.post(reverse("future_plans:action_toggle", args=[a.pk]))
    a.refresh_from_db(); assert not a.is_done and a.completed_at is None
    assert auth_client.get(reverse("future_plans:action_delete", args=[a.pk])).status_code == 200 and PlanAction.objects.exists()
    auth_client.post(reverse("future_plans:action_delete", args=[a.pk]))
    assert not PlanAction.objects.exists()
    r = auth_client.post(reverse("future_plans:action_add", args=[p.pk]), {"title": ""}, follow=True)
    assert "Not added" in r.content.decode() and not PlanAction.objects.exists()


def test_open_actions_listed_before_done_ones(auth_client, user, make_plan):
    p = make_plan(user)
    PlanAction.objects.create(owner=user, plan=p, title="Finished step", is_done=True)
    PlanAction.objects.create(owner=user, plan=p, title="Pending step")
    h = auth_client.get(reverse("future_plans:detail", args=[p.pk])).content.decode()
    assert h.index("Pending step") < h.index("Finished step")


# ------------------------------------------------------------ affordability
def test_average_monthly_net_uses_last_three_full_months(user, make_account, make_tx):
    acc = make_account(user, opening="0")
    t = dt.date(2026, 6, 15)
    assert average_monthly_net(user, "GBP", t) is None                                   # no activity -> no suggestion
    make_tx(user, T.INCOME, "3000", acc, date=dt.date(2026, 3, 10)); make_tx(user, T.EXPENSE, "600", acc, date=dt.date(2026, 3, 11))
    make_tx(user, T.INCOME, "3000", acc, date=dt.date(2026, 4, 10)); make_tx(user, T.EXPENSE, "900", acc, date=dt.date(2026, 5, 11))
    make_tx(user, T.INCOME, "99999", acc, date=dt.date(2026, 6, 2))                       # current month excluded
    make_tx(user, T.INCOME, "99999", acc, date=dt.date(2026, 2, 28))                      # older than 3 months excluded
    # Mar +2400, Apr +3000, May -900  => (2400 + 3000 - 900) / 3 = 1500
    assert average_monthly_net(user, "GBP", t) == D("1500.00")


def test_affordability_page_states_and_wording(auth_client, user):
    url = reverse("future_plans:affordability")
    base = {"cost": "12000", "saved": "2000", "target_date": (local_today(user) + dt.timedelta(days=365)).isoformat()}
    short = auth_client.get(url, {**base, "monthly_available": "400"}).content.decode()
    assert "per month short" in short and "Estimate only" in short and "Not financial advice" in short
    ok = auth_client.get(url, {**base, "monthly_available": "5000"}).content.decode()
    assert "covers what's needed" in ok and "Real life may differ" in ok
    result_section = ok.split('aria-live="polite"')[1]                       # the computed result, not the page's disclaimer
    assert "affordable" not in result_section.lower() and "guarantee" not in result_section.lower()
    assert "can't tell you whether something is truly affordable" in ok     # the disclaimer itself is present
    none = auth_client.get(url, {**base, "monthly_available": "0"}).content.decode()
    assert "never reach the target" in none
    done = auth_client.get(url, {"cost": "100", "saved": "100", "monthly_available": "5"}).content.decode()
    assert "already covers the cost" in done
    for bad in ({"cost": "0"}, {"cost": "abc"}, {"cost": "10", "saved": "-5"}, {"cost": "10", "monthly_available": "-1"}):
        r = auth_client.get(url, bad)
        assert r.status_code == 200 and "What these assumptions imply" not in r.content.decode()


def test_affordability_prefills_from_plan_and_suggests_cash_flow(auth_client, user, make_plan, make_savings, make_account, make_tx):
    t = local_today(user)
    acc = make_account(user)
    pm = (t.replace(day=1) - DAY)
    make_tx(user, T.INCOME, "900", acc, date=pm.replace(day=5))
    sg = make_savings(user, target="5000"); contribute(user, sg, 400)
    p = make_plan(user, title="Course", estimated_cost=D("5000"), savings_goal=sg, target_date=t + dt.timedelta(days=300))
    h = auth_client.get(reverse("future_plans:plan_affordability", args=[p.pk])).content.decode()
    assert 'value="5000.00"' in h and 'value="400.00"' in h and "Pre-filled from your average net cash flow" in h and "Course" in h


def test_affordability_has_no_suggestion_without_data_or_when_negative(auth_client, user, make_account, make_tx):
    assert "Pre-filled" not in auth_client.get(reverse("future_plans:affordability")).content.decode()
    acc = make_account(user); t = local_today(user)
    pm = (t.replace(day=1) - DAY)
    make_tx(user, T.EXPENSE, "500", acc, date=pm.replace(day=5))
    h = auth_client.get(reverse("future_plans:affordability")).content.decode()
    assert 'name="monthly_available" value=' not in h.replace('value=""', "")


# ------------------------------------------------------------- dashboard
def test_dashboard_shows_only_open_plans_within_ninety_days(auth_client, user, make_plan):
    t = local_today(user)
    make_plan(user, title="Soon plan", target_date=t + dt.timedelta(days=30))
    make_plan(user, title="Far plan", target_date=t + dt.timedelta(days=400))
    make_plan(user, title="Done plan", status="achieved", target_date=t + dt.timedelta(days=10))
    make_plan(user, title="Dropped plan", status="cancelled", target_date=t + dt.timedelta(days=10))
    make_plan(user, title="Undated plan")
    make_plan(user, title="Overdue plan", target_date=t - dt.timedelta(days=5))
    h = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Soon plan" in h and "Overdue plan" in h and "Target date passed" in h
    for hidden in ("Far plan", "Done plan", "Dropped plan", "Undated plan"):
        assert hidden not in h, hidden
    assert [p.title for p in approaching_plans(user, t)] == ["Overdue plan", "Soon plan"]


def test_dashboard_plan_progress_matches_plan_page(auth_client, user, make_plan, make_savings):
    t = local_today(user)
    sg = make_savings(user, target="400"); contribute(user, sg, 100)
    make_plan(user, title="Linked", savings_goal=sg, target_date=t + dt.timedelta(days=40))
    assert 'value="25"' in auth_client.get(reverse("dashboard:home")).content.decode()


# --------------------------------------------------------- ownership
@pytest.fixture
def alice_plan(user, make_plan, make_savings, make_goal):
    sg, g = make_savings(user, "AliceSavings"), make_goal(user, title="AliceGoal")
    p = make_plan(user, title="AlicePlan", estimated_cost=D("500"), savings_goal=sg, goal=g)
    a = PlanAction.objects.create(owner=user, plan=p, title="AliceAction")
    return p, a, sg, g


def test_other_user_cannot_touch_alices_plans(other_user, alice_plan):
    p, a, sg, g = alice_plan
    bob = Client(); bob.force_login(other_user)
    for name, args in (("detail", [p.pk]), ("update", [p.pk]), ("delete", [p.pk]), ("plan_affordability", [p.pk])):
        assert bob.get(reverse(f"future_plans:{name}", args=args)).status_code == 404, name
    posts = [("update", [p.pk]), ("delete", [p.pk]), ("create_savings", [p.pk]), ("action_add", [p.pk]), ("action_toggle", [a.pk]), ("action_delete", [a.pk])]
    for name, args in posts:
        assert bob.post(reverse(f"future_plans:{name}", args=args), {"title": "HACKED", "confirm": "on"}).status_code == 404, name
    assert bob.get(reverse("future_plans:action_delete", args=[a.pk])).status_code == 404
    p.refresh_from_db(); a.refresh_from_db()
    assert p.title == "AlicePlan" and not a.is_done and FuturePlan.objects.filter(pk=p.pk).exists() and PlanAction.objects.filter(pk=a.pk).exists()
    for name in ("future_plans:list", "dashboard:home", "future_plans:affordability"):
        body = bob.get(reverse(name)).content.decode()
        assert "AlicePlan" not in body and "AliceAction" not in body


def test_other_user_cannot_link_alices_goal_or_savings_goal(other_user, alice_plan):
    p, a, sg, g = alice_plan
    bob = Client(); bob.force_login(other_user)
    r = bob.post(reverse("future_plans:create"), plan_data(savings_goal=sg.pk))
    assert r.status_code == 200 and not FuturePlan.objects.filter(owner=other_user).exists()
    r = bob.post(reverse("future_plans:create"), plan_data(goal=g.pk))
    assert r.status_code == 200 and not FuturePlan.objects.filter(owner=other_user).exists()
    body = bob.get(reverse("future_plans:create")).content.decode()
    assert "AliceSavings" not in body and "AliceGoal" not in body


def test_anonymous_and_csrf(client, user, alice_plan):
    p, a, sg, g = alice_plan
    for name, args in (("list", []), ("create", []), ("detail", [p.pk]), ("affordability", []), ("plan_affordability", [p.pk])):
        r = client.get(reverse(f"future_plans:{name}", args=args))
        assert r.status_code == 302 and r.url.startswith(reverse("accounts:login")), name
    c = Client(enforce_csrf_checks=True); c.force_login(user)
    for name, args in (("create_savings", [p.pk]), ("action_add", [p.pk]), ("action_toggle", [a.pk]), ("delete", [p.pk])):
        assert c.post(reverse(f"future_plans:{name}", args=args), {}).status_code == 403, name


def test_export_and_account_deletion_cover_plans(auth_client, client, user, other_user, password, alice_plan, make_plan):
    p, a, sg, g = alice_plan
    make_plan(other_user, title="BobSecretPlan")
    data = json.loads(auth_client.get(reverse("accounts:export")).content)
    assert [x["title"] for x in data["future_plans"]] == ["AlicePlan"] and data["plan_actions"][0]["title"] == "AliceAction"
    assert "BobSecretPlan" not in json.dumps(data) and data["future_plans"][0]["savings_goal_id"] == sg.pk
    client.force_login(user)
    assert client.post(reverse("accounts:delete"), {"password": password, "confirm": "on"}).status_code == 302
    assert not FuturePlan.objects.filter(owner_id=user.pk).exists() and not PlanAction.objects.filter(owner_id=user.pk).exists()
    assert FuturePlan.objects.filter(owner=other_user).exists()


def test_achieved_date_uses_the_users_local_calendar_date(auth_client, user, make_plan, monkeypatch):
    from django.utils import timezone
    monkeypatch.setattr(timezone, "now", lambda: dt.datetime(2026, 3, 1, 23, 30, tzinfo=dt.timezone.utc))
    user.profile.timezone = "Pacific/Auckland"; user.profile.save()
    p = make_plan(user)
    auth_client.post(reverse("future_plans:update", args=[p.pk]), plan_data(status="achieved"))
    p.refresh_from_db()
    assert p.achieved_on == dt.date(2026, 3, 2)
