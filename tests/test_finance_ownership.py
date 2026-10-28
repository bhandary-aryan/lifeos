"""Bob must not see, change or delete Alice's financial records, however he guesses IDs."""
import datetime as dt
from decimal import Decimal as D

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.services import local_today
from apps.finance import services as fin
from apps.finance.models import (
    Account, Budget, Category, Debt, DebtDrawdown, SavingsContribution, SavingsGoal, Transaction)

pytestmark = pytest.mark.django_db
T = Transaction.Type


@pytest.fixture
def alice(user, make_account, make_tx, make_debt, make_savings, cat):
    cur, sav = make_account(user, "AliceMain", "1000"), make_account(user, "AliceSavings")
    debt = make_debt(user, "AliceBank", original="800")
    tx = make_tx(user, T.EXPENSE, "20", cur, description="AliceSecretLunch", category=cat(user, "Groceries"))
    pay = make_tx(user, T.DEBT_PAYMENT, "50", cur, debt=debt, principal_amount="40", interest_amount="10")
    goal = make_savings(user, "AliceGoal")
    contrib = fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=local_today(user), amount=D("30")), cur, sav)
    draw = DebtDrawdown.objects.create(owner=user, debt=debt, date=local_today(user), amount=D("10"))
    budget = Budget.objects.create(owner=user, category=cat(user, "Groceries"), year=2026, month=3, currency="GBP", limit_amount=D("100"))
    return dict(cur=cur, sav=sav, debt=debt, tx=tx, pay=pay, goal=goal, contrib=contrib, draw=draw, budget=budget, cat=cat(user, "Transport"))


@pytest.fixture
def bob(other_user):
    c = Client(); c.force_login(other_user); return c


def endpoints(a):
    g = lambda name, *args: reverse(name, args=args)   # noqa: E731
    gets = [
        g("finance:account_update", a["cur"].pk), g("finance:account_delete", a["cur"].pk),
        g("finance:transaction_update", a["tx"].pk), g("finance:transaction_delete", a["tx"].pk),
        g("finance:transaction_update", a["pay"].pk), g("finance:transaction_delete", a["pay"].pk),
        g("finance:budget_update", a["budget"].pk), g("finance:budget_delete", a["budget"].pk),
        g("finance:debt_detail", a["debt"].pk), g("finance:debt_update", a["debt"].pk), g("finance:debt_delete", a["debt"].pk),
        g("finance:drawdown_delete", a["draw"].pk),
        g("finance:savings_detail", a["goal"].pk), g("finance:savings_update", a["goal"].pk), g("finance:savings_delete", a["goal"].pk),
        g("finance:contribution_create", a["goal"].pk), g("finance:contribution_update", a["contrib"].pk), g("finance:contribution_delete", a["contrib"].pk),
    ]
    posts = gets + [g("finance:category_delete", a["cat"].pk), g("finance:drawdown_add", a["debt"].pk)]
    return gets, posts


def snapshot():
    return (list(Account.objects.values_list("pk", "name", "opening_balance", "currency", "is_active")),
            list(Transaction.objects.values_list("pk", "amount", "description", "account_id", "type")),
            list(Debt.objects.values_list("pk", "creditor", "original_amount", "status")),
            list(DebtDrawdown.objects.values_list("pk", "amount")), list(Budget.objects.values_list("pk", "limit_amount")),
            list(SavingsGoal.objects.values_list("pk", "name", "target_amount")),
            list(SavingsContribution.objects.values_list("pk", "amount", "transfer_id")), Category.objects.count())


def test_other_user_gets_404_everywhere_and_nothing_changes(bob, alice):
    before = snapshot()
    gets, posts = endpoints(alice)
    for url in gets:
        assert bob.get(url).status_code == 404, f"GET {url}"
    payload = {"name": "HACKED", "amount": "1", "creditor": "HACKED", "date": "2026-01-01", "description": "HACKED", "type": "expense",
               "target_amount": "1", "limit_amount": "1", "original_amount": "1", "confirm": "on"}
    for url in posts:
        assert bob.post(url, payload).status_code == 404, f"POST {url}"
    assert snapshot() == before


def test_anonymous_redirected_to_login_for_all_finance_urls(client, alice):
    before = snapshot()
    gets, posts = endpoints(alice)
    for name in ("finance:overview", "finance:reports", "finance:accounts", "finance:categories", "finance:transactions", "finance:transactions_export",
                 "finance:budgets", "finance:debts", "finance:debt_planner", "finance:savings", "finance:transaction_create", "finance:account_create"):
        gets = gets + [reverse(name)]
    for url in gets:
        r = client.get(url)
        assert r.status_code == 302 and r.url.startswith(reverse("accounts:login")), url
    for url in posts:
        r = client.post(url, {"name": "x"})
        assert r.status_code == 302 and r.url.startswith(reverse("accounts:login")), url
    assert snapshot() == before


def test_404_bodies_do_not_leak_alices_data(bob, alice):
    for url in endpoints(alice)[0]:
        body = bob.get(url).content.decode()
        for secret in ("AliceBank", "AliceGoal", "AliceSecretLunch", "AliceMain", "AliceSavings"):
            assert secret not in body, (url, secret)


def test_bobs_pages_never_show_alices_records(bob, alice):
    for name in ("finance:overview", "finance:reports", "finance:accounts", "finance:transactions", "finance:budgets", "finance:debts",
                 "finance:savings", "finance:categories", "finance:debt_planner", "dashboard:home", "finance:transaction_create"):
        body = bob.get(reverse(name), {"month": "2026-03", "currency": "GBP", "monthly_budget": "100"}).content.decode()
        for secret in ("AliceBank", "AliceGoal", "AliceSecretLunch", "AliceMain", "AliceSavings"):
            assert secret not in body, (name, secret)


def test_bobs_balances_and_totals_ignore_alices_money(bob, other_user, alice, make_account):
    mine = make_account(other_user, "BobAcct", "5")
    assert fin.account_balances(other_user) == {mine.pk: D("5.00")}
    t = local_today(other_user)
    assert fin.month_summary(other_user, "GBP", t.year, t.month)["spending"] == D("0.00")
    assert fin.goals_with_progress(other_user, t) == [] and fin.debts_with_balances(other_user) == []


def test_filters_cannot_reach_alices_data_by_id(bob, alice):
    html = bob.get(reverse("finance:transactions"), {"account": alice["cur"].pk, "category": alice["cat"].pk}).content.decode()
    assert "AliceSecretLunch" not in html and "Payment to" not in html
    csv_body = bob.get(reverse("finance:transactions_export"), {"account": alice["cur"].pk}).content.decode()
    assert "AliceSecretLunch" not in csv_body and csv_body.count("\n") == 1


def test_bob_cannot_reference_alices_records_in_forms(bob, other_user, alice, make_account, make_debt):
    mine = make_account(other_user, "BobAcct", "100"); mydebt = make_debt(other_user, "BobBank")
    url = reverse("finance:transaction_create")
    base = {"date": "2026-03-01", "amount": "10"}
    cases = [
        {**base, "type": "expense", "account": alice["cur"].pk},
        {**base, "type": "income", "account": mine.pk, "category": Category.objects.get(owner=alice["cur"].owner, name="Salary", kind="income").pk},
        {**base, "type": "transfer", "account": mine.pk, "destination_account": alice["sav"].pk},
        {**base, "type": "transfer", "account": alice["cur"].pk, "destination_account": mine.pk},
        {**base, "type": "debt_payment", "account": mine.pk, "debt": alice["debt"].pk, "principal_amount": "10", "interest_amount": "0"},
    ]
    before = Transaction.objects.count()
    for data in cases:
        assert bob.post(url, data).status_code == 200, data         # re-rendered with errors, not saved
    assert Transaction.objects.count() == before
    del mydebt


def test_bob_cannot_budget_or_contribute_using_alices_objects(bob, other_user, alice, make_account, make_savings):
    r = bob.post(reverse("finance:budget_create"), {"category": alice["cat"].pk, "year": 2026, "month": 5, "currency": "GBP", "limit_amount": "10"})
    assert r.status_code == 200 and Budget.objects.filter(owner=other_user).count() == 0
    mine_a, mine_b, goal = make_account(other_user, "B1"), make_account(other_user, "B2"), make_savings(other_user, "BobGoal")
    r = bob.post(reverse("finance:contribution_create", args=[goal.pk]), {"date": "2026-03-01", "amount": "5", "from_account": alice["cur"].pk, "to_account": mine_b.pk})
    assert r.status_code == 200 and not SavingsContribution.objects.filter(goal=goal).exists()
    assert bob.post(reverse("finance:contribution_create", args=[alice["goal"].pk]), {"date": "2026-03-01", "amount": "5"}).status_code == 404
    del mine_a


def test_query_string_ids_do_not_prefill_alices_debt(bob, alice):
    body = bob.get(reverse("finance:transaction_create"), {"debt": alice["debt"].pk}).content.decode()
    assert "AliceBank" not in body


def test_model_level_ownership_checks(other_user, alice, make_account):
    from django.core.exceptions import ValidationError
    mine = make_account(other_user, "BobAcct")
    with pytest.raises(ValidationError):
        Transaction(owner=other_user, type=T.EXPENSE, amount=D("1"), date=local_today(other_user), account=alice["cur"]).full_clean()
    with pytest.raises(ValidationError):
        Transaction(owner=other_user, type=T.TRANSFER, amount=D("1"), date=local_today(other_user), account=mine, destination_account=alice["cur"]).full_clean()
    with pytest.raises(ValidationError):
        SavingsContribution(owner=other_user, goal=alice["goal"], date=local_today(other_user), amount=D("1")).full_clean(exclude=["transfer"])
    with pytest.raises(ValidationError):
        DebtDrawdown(owner=other_user, debt=alice["debt"], date=local_today(other_user), amount=D("1")).full_clean()
    with pytest.raises(ValidationError):
        Budget(owner=other_user, category=alice["cat"], year=2026, month=1, currency="GBP", limit_amount=D("1")).full_clean()


def test_state_changing_finance_endpoints_enforce_csrf(user, alice):
    c = Client(enforce_csrf_checks=True); c.force_login(user)
    _, posts = endpoints(alice)
    for url in posts + [reverse("finance:transaction_create"), reverse("finance:categories"), reverse("finance:account_create")]:
        assert c.post(url, {}).status_code == 403, url
    assert Transaction.objects.count() == 2 + 1                          # untouched (expense, payment, linked transfer)


def test_delete_pages_require_post_to_delete(bob, auth_client, alice):
    """GET on a delete URL only shows a confirmation; it must never delete."""
    for name, key in (("finance:transaction_delete", "tx"), ("finance:debt_delete", "debt"), ("finance:account_delete", "cur"),
                      ("finance:budget_delete", "budget"), ("finance:savings_delete", "goal")):
        assert auth_client.get(reverse(name, args=[alice[key].pk])).status_code == 200
    assert Transaction.objects.filter(pk=alice["tx"].pk).exists() and Debt.objects.filter(pk=alice["debt"].pk).exists()
    assert Budget.objects.filter(pk=alice["budget"].pk).exists() and SavingsGoal.objects.filter(pk=alice["goal"].pk).exists()
