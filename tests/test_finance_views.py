import csv
import datetime as dt
import io
import json
from decimal import Decimal as D

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.services import local_today
from apps.core.templatetags.money import money as money_filter
from apps.finance import services as fin
from apps.finance.models import (
    Account, Budget, Category, Debt, DebtDrawdown, SavingsContribution, SavingsGoal, Transaction)

pytestmark = pytest.mark.django_db
T = Transaction.Type
D1 = dt.timedelta(days=1)


def tx_data(account, **over):
    d = {"type": "expense", "date": local_today(account.owner).isoformat(), "amount": "12.50", "account": account.pk, "description": "Lunch"}
    d.update(over)
    return d


# ---------------------------------------------------------------- pages
def test_money_filter():
    assert money_filter(D("1234.5"), "GBP") == "£1,234.50"
    assert money_filter(D("-12"), "USD") == "-$12.00"
    assert money_filter(D("0.005"), "EUR") == "€0.01"
    assert money_filter("junk") == ""


FINANCE_PAGES = ["finance:overview", "finance:reports", "finance:accounts", "finance:categories", "finance:transactions", "finance:budgets",
                 "finance:debts", "finance:debt_planner", "finance:savings", "finance:account_create", "finance:transaction_create",
                 "finance:budget_create", "finance:debt_create", "finance:savings_create"]


@pytest.mark.parametrize("name", FINANCE_PAGES)
def test_every_finance_page_loads_for_a_new_user(auth_client, name):
    assert auth_client.get(reverse(name)).status_code == 200


def test_empty_states(auth_client):
    assert "Start by adding an account" in auth_client.get(reverse("finance:overview")).content.decode()
    assert "No accounts yet" in auth_client.get(reverse("finance:accounts")).content.decode()
    assert "No transactions yet" in auth_client.get(reverse("finance:transactions")).content.decode()
    assert "No debts recorded" in auth_client.get(reverse("finance:debts")).content.decode()
    assert "No savings goals yet" in auth_client.get(reverse("finance:savings")).content.decode()
    assert "No budgets for" in auth_client.get(reverse("finance:budgets")).content.decode()


def test_nav_highlights_finances_section(auth_client):
    assert 'aria-current="page"' in auth_client.get(reverse("finance:debts")).content.decode()


@pytest.mark.parametrize("params", [{"month": "garbage"}, {"month": "2026-13"}, {"currency": "XXX"}, {"month": "2026-02", "currency": "USD"}])
def test_overview_and_reports_tolerate_bad_parameters(auth_client, params):
    assert auth_client.get(reverse("finance:overview"), params).status_code == 200
    assert auth_client.get(reverse("finance:reports"), params).status_code == 200
    assert auth_client.get(reverse("finance:reports"), {"year": "99999"}).status_code == 200


# ---------------------------------------------------------- transactions
def test_create_each_transaction_type_through_the_form(auth_client, user, make_account, make_debt, cat):
    cur, sav, debt = make_account(user, "Current", "1000"), make_account(user, "Savings"), make_debt(user, original="500")
    url = reverse("finance:transaction_create")
    assert auth_client.post(url, tx_data(cur, type="income", amount="200", category=cat(user, "Salary", "income").pk)).status_code == 302
    assert auth_client.post(url, tx_data(cur, amount="30", category=cat(user, "Groceries").pk)).status_code == 302
    assert auth_client.post(url, tx_data(cur, type="transfer", amount="100", destination_account=sav.pk)).status_code == 302
    assert auth_client.post(url, tx_data(cur, type="debt_payment", amount="60", debt=debt.pk, principal_amount="50", interest_amount="10")).status_code == 302
    assert Transaction.objects.filter(owner=user).count() == 4
    bal = fin.account_balances(user)
    assert bal[cur.pk] == D("1000") + 200 - 30 - 100 - 60 and bal[sav.pk] == D("100.00")
    assert fin.outstanding_principal(debt) == D("450.00")


@pytest.mark.parametrize("over,message", [
    ({"amount": "0"}, "greater than zero"), ({"amount": "-5"}, "greater than zero"), ({"amount": ""}, "required"),
    ({"amount": "1.234"}, "decimal places"), ({"amount": "abc"}, "number"),
    ({"type": "transfer"}, "Choose the account the money moves to"),
])
def test_invalid_transactions_show_clear_errors_and_save_nothing(auth_client, user, make_account, over, message):
    a = make_account(user)
    r = auth_client.post(reverse("finance:transaction_create"), tx_data(a, **over))
    assert r.status_code == 200 and message.lower() in r.content.decode().lower() and not Transaction.objects.exists()


def test_transfer_to_same_account_and_unreconciled_payment_rejected(auth_client, user, make_account, make_debt):
    a, debt = make_account(user), make_debt(user)
    r = auth_client.post(reverse("finance:transaction_create"), tx_data(a, type="transfer", destination_account=a.pk))
    assert "two different accounts" in r.content.decode() and not Transaction.objects.exists()
    r = auth_client.post(reverse("finance:transaction_create"), tx_data(a, type="debt_payment", amount="100", debt=debt.pk, principal_amount="80", interest_amount="10"))
    assert "must equal the total" in r.content.decode() and not Transaction.objects.exists()
    r = auth_client.post(reverse("finance:transaction_create"), tx_data(a, type="debt_payment", amount="100", debt=debt.pk))
    assert r.status_code == 200 and not Transaction.objects.exists()           # principal/interest required


def test_overpaying_debt_through_the_form_is_blocked(auth_client, user, make_account, make_debt):
    a, debt = make_account(user, opening="999"), make_debt(user, original="100")
    r = auth_client.post(reverse("finance:transaction_create"), tx_data(a, type="debt_payment", amount="150", debt=debt.pk, principal_amount="150", interest_amount="0"))
    assert "outstanding principal" in r.content.decode() and not Transaction.objects.exists()


def test_irrelevant_fields_are_ignored_per_type(auth_client, user, make_account, make_debt):
    a, b, debt = make_account(user, "A"), make_account(user, "B"), make_debt(user)
    auth_client.post(reverse("finance:transaction_create"), tx_data(a, destination_account=b.pk, debt=debt.pk, principal_amount="5", interest_amount="5"))
    t = Transaction.objects.get()
    assert (t.destination_account, t.debt, t.principal_amount, t.interest_amount) == (None, None, 0, 0)


def test_edit_and_delete_through_views_recalculate_balances(auth_client, user, make_account, make_tx):
    a = make_account(user, opening="100")
    t = make_tx(user, T.EXPENSE, "40", a)
    auth_client.post(reverse("finance:transaction_update", args=[t.pk]), tx_data(a, amount="10"))
    assert fin.account_balances(user)[a.pk] == D("90.00")
    assert auth_client.get(reverse("finance:transaction_delete", args=[t.pk])).status_code == 200
    assert Transaction.objects.exists()
    auth_client.post(reverse("finance:transaction_delete", args=[t.pk]))
    assert fin.account_balances(user)[a.pk] == D("100.00")


def test_list_filters_search_sort_and_pagination(auth_client, user, make_account, make_tx, cat):
    a, b = make_account(user, "Alpha", "5000"), make_account(user, "Bravo", "5000")
    d = local_today(user)
    make_tx(user, T.EXPENSE, "10", a, date=d - dt.timedelta(days=3), description="Coffee beans", category=cat(user, "Groceries"))
    make_tx(user, T.EXPENSE, "200", b, date=d - dt.timedelta(days=2), description="Train pass", category=cat(user, "Transport"))
    make_tx(user, T.INCOME, "999", a, date=d - D1, description="Payday")
    make_tx(user, T.TRANSFER, "50", a, date=d, destination_account=b, description="Move")
    url = reverse("finance:transactions")
    def seen(**q):
        html = auth_client.get(url, q).content.decode()
        return {n for n in ("Coffee beans", "Train pass", "Payday", "Move") if n in html}
    assert seen() == {"Coffee beans", "Train pass", "Payday", "Move"}
    assert seen(type="expense") == {"Coffee beans", "Train pass"}
    assert seen(account=b.pk) == {"Train pass", "Move"}                      # destination side included
    assert seen(category=cat(user, "Transport").pk) == {"Train pass"}
    assert seen(q="coffee") == {"Coffee beans"} and seen(q="transport") == {"Train pass"}
    assert seen(date_from=(d - dt.timedelta(days=2)).isoformat(), date_to=(d - D1).isoformat()) == {"Train pass", "Payday"}
    assert seen(type="bogus") == {"Coffee beans", "Train pass", "Payday", "Move"}      # invalid filter ignored
    html = auth_client.get(url, {"sort": "amount"}).content.decode()
    assert html.index("Payday") < html.index("Train pass") < html.index("Move") < html.index("Coffee beans")
    for i in range(30):
        make_tx(user, T.INCOME, "1", a, description=f"bulk{i:02d}")
    assert "Page 1 of 2" in auth_client.get(url).content.decode()
    assert auth_client.get(url, {"page": 2}).status_code == 200


def test_linked_savings_transfer_is_protected_in_the_ui(auth_client, user, make_account, make_savings):
    cur, sav, goal = make_account(user, "Cur", "500"), make_account(user, "Sav"), make_savings(user)
    c = fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=local_today(user), amount=D("100")), cur, sav)
    html = auth_client.get(reverse("finance:transactions")).content.decode()
    assert "Savings" in html and reverse("finance:transaction_update", args=[c.transfer_id]) not in html
    r = auth_client.get(reverse("finance:transaction_update", args=[c.transfer_id]))
    assert r.status_code == 302 and r.url == reverse("finance:savings_detail", args=[goal.pk])
    auth_client.post(reverse("finance:transaction_delete", args=[c.transfer_id]))
    assert Transaction.objects.filter(pk=c.transfer_id).exists()


def test_inactive_accounts_not_offered_for_new_transactions(auth_client, user, make_account):
    make_account(user, "Active"); make_account(user, "Retired", is_active=False)
    html = auth_client.get(reverse("finance:transaction_create")).content.decode()
    assert "Active (GBP)" in html and "Retired (GBP)" not in html


# ------------------------------------------------------------- accounts/categories
def test_account_crud_and_guards(auth_client, user, make_account, make_tx):
    r = auth_client.post(reverse("finance:account_create"), {"name": "Wallet", "account_type": "cash", "currency": "GBP", "opening_balance": "25.50", "is_active": "on"})
    a = Account.objects.get(owner=user, name="Wallet")
    assert r.status_code == 302 and a.opening_balance == D("25.50")
    dup = auth_client.post(reverse("finance:account_create"), {"name": "wallet", "account_type": "cash", "currency": "GBP", "opening_balance": "0"})
    assert dup.status_code == 200 and "already have an account" in dup.content.decode()
    make_tx(user, T.INCOME, "5", a)
    r = auth_client.post(reverse("finance:account_update", args=[a.pk]), {"name": "Wallet", "account_type": "cash", "currency": "USD", "opening_balance": "25.50", "is_active": "on"})
    assert "once an account has transactions" in r.content.decode() and Account.objects.get(pk=a.pk).currency == "GBP"
    auth_client.post(reverse("finance:account_delete", args=[a.pk]))
    assert Account.objects.filter(pk=a.pk).exists()                                  # protected, not a crash
    assert auth_client.post(reverse("finance:account_delete", args=[make_account(user, "Empty").pk])).status_code == 302
    assert not Account.objects.filter(name="Empty").exists()


def test_category_management(auth_client, user, cat, make_account, make_tx):
    url = reverse("finance:categories")
    auth_client.post(url, {"name": "Pets", "kind": "expense"})
    pets = Category.objects.get(owner=user, name="Pets")
    assert "already have a category" in auth_client.post(url, {"name": "pets", "kind": "expense"}).content.decode()
    a = make_account(user, opening="100")
    t = make_tx(user, T.EXPENSE, "5", a, category=pets)
    Budget.objects.create(owner=user, category=pets, year=2026, month=1, currency="GBP", limit_amount=D("10"))
    interest = cat(user, "Debt interest")
    auth_client.post(reverse("finance:category_delete", args=[interest.pk]))
    assert Category.objects.filter(pk=interest.pk).exists()                          # system category protected
    auth_client.post(reverse("finance:category_delete", args=[pets.pk]))
    t.refresh_from_db()
    assert t.category is None and not Budget.objects.filter(category_id=pets.pk).exists() and Transaction.objects.filter(pk=t.pk).exists()


# ---------------------------------------------------------------- budgets
def test_budget_form_flow_and_duplicates(auth_client, user, cat):
    c = cat(user, "Groceries")
    data = {"category": c.pk, "year": 2026, "month": 3, "currency": "GBP", "limit_amount": "250.00"}
    assert auth_client.post(reverse("finance:budget_create"), data).status_code == 302
    r = auth_client.post(reverse("finance:budget_create"), data)
    assert r.status_code == 200 and "already have a budget" in r.content.decode() and Budget.objects.count() == 1
    assert auth_client.post(reverse("finance:budget_create"), {**data, "category": cat(user, "Salary", "income").pk, "month": 4}).status_code == 200
    assert Budget.objects.count() == 1
    assert auth_client.post(reverse("finance:budget_create"), {**data, "month": 5, "limit_amount": "0"}).status_code == 200


def test_budget_page_shows_warning_and_over_states(auth_client, user, make_account, make_tx, cat):
    a = make_account(user, opening="9999")
    d = dt.date(2026, 3, 10)
    g, t = cat(user, "Groceries"), cat(user, "Transport")
    Budget.objects.create(owner=user, category=g, year=2026, month=3, currency="GBP", limit_amount=D("100"))
    Budget.objects.create(owner=user, category=t, year=2026, month=3, currency="GBP", limit_amount=D("100"))
    make_tx(user, T.EXPENSE, "85", a, date=d, category=g); make_tx(user, T.EXPENSE, "130", a, date=d, category=t)
    html = auth_client.get(reverse("finance:budgets"), {"month": "2026-03"}).content.decode()
    assert "approaching the limit" in html and "over budget by £30.00" in html


# ------------------------------------------------------------------ debts
def test_debt_flow_pages_and_guards(auth_client, user, make_account, make_tx):
    a = make_account(user, opening="1000")
    r = auth_client.post(reverse("finance:debt_create"), {"creditor": "Car loan", "currency": "GBP", "original_amount": "2000", "interest_rate": "6.5",
                                                           "minimum_payment": "100", "due_day": "15", "start_date": "2026-01-01", "status": "active"})
    debt = Debt.objects.get(owner=user)
    assert r.status_code == 302 and r.url == reverse("finance:debt_detail", args=[debt.pk])
    form = auth_client.get(reverse("finance:transaction_create"), {"debt": debt.pk}).content.decode()
    assert 'x-data="{ type: \'debt_payment\'' in form
    auth_client.post(reverse("finance:transaction_create"), tx_data(a, type="debt_payment", amount="120", debt=debt.pk, principal_amount="100", interest_amount="20"))
    html = auth_client.get(reverse("finance:debt_detail", args=[debt.pk])).content.decode()
    assert "£1,900.00" in html and "Payment history" in html and "£20.00" in html
    bad = auth_client.post(reverse("finance:debt_update", args=[debt.pk]), {"creditor": "Car loan", "currency": "GBP", "original_amount": "50", "interest_rate": "6.5",
                                                                          "minimum_payment": "100", "start_date": "2026-01-01", "status": "active"})
    assert bad.status_code == 200 and "lower than the principal already repaid" in bad.content.decode()
    assert Debt.objects.get(pk=debt.pk).original_amount == D("2000.00")
    auth_client.post(reverse("finance:drawdown_add", args=[debt.pk]), {"date": local_today(user).isoformat(), "amount": "300"})
    assert fin.outstanding_principal(debt) == D("2200.00")
    auth_client.post(reverse("finance:debt_delete", args=[debt.pk]))
    assert Debt.objects.filter(pk=debt.pk).exists()                                   # has payments -> restricted, not a crash


@pytest.mark.parametrize("over", [{"interest_rate": "101"}, {"interest_rate": "-1"}, {"original_amount": "0"}, {"due_day": "32"}, {"minimum_payment": "-5"},
                                  {"expected_payoff_date": "2020-01-01"}])
def test_debt_form_validation(auth_client, over):
    data = {"creditor": "X", "currency": "GBP", "original_amount": "100", "interest_rate": "5", "minimum_payment": "10", "start_date": "2026-01-01", "status": "active"}
    data.update(over)
    assert auth_client.post(reverse("finance:debt_create"), data).status_code == 200 and not Debt.objects.exists()


def test_debt_list_totals_by_currency_and_paid_off_excluded(auth_client, user, make_debt, make_account, make_tx):
    make_debt(user, "GBP loan", original="500"); make_debt(user, "USD loan", original="300", currency="USD")
    done = make_debt(user, "Done", original="100"); a = make_account(user, opening="500")
    make_tx(user, T.DEBT_PAYMENT, "100", a, debt=done, principal_amount="100", interest_amount="0")
    html = auth_client.get(reverse("finance:debts")).content.decode()
    assert "£500.00" in html and "$300.00" in html and "Paid off" in html


def test_repayment_planner_page(auth_client, user, make_debt):
    make_debt(user, "Card", original="1000", interest_rate=D("20"), minimum_payment=D("30"))
    make_debt(user, "Loan", original="400", interest_rate=D("5"), minimum_payment=D("20"))
    html = auth_client.get(reverse("finance:debt_planner"), {"currency": "GBP", "monthly_budget": "200"}).content.decode()
    assert "Avalanche" in html and "Snowball" in html and "Estimates only" in html and "not personalised financial advice" in html
    low = auth_client.get(reverse("finance:debt_planner"), {"currency": "GBP", "monthly_budget": "10"}).content.decode()
    assert "below the combined minimum payments" in low
    none = auth_client.get(reverse("finance:debt_planner"), {"currency": "USD", "monthly_budget": "100"}).content.decode()
    assert "no active debts" in none.lower()
    assert auth_client.get(reverse("finance:debt_planner"), {"currency": "GBP", "monthly_budget": "-5"}).status_code == 200


# ---------------------------------------------------------------- savings
def test_savings_flow_through_views(auth_client, user, make_account):
    cur, sav = make_account(user, "Cur", "500"), make_account(user, "Sav", account_type="savings")
    t = local_today(user)
    auth_client.post(reverse("finance:savings_create"), {"name": "Holiday", "currency": "GBP", "target_amount": "1200", "target_date": (t + dt.timedelta(days=365)).isoformat(),
                                                         "priority": "medium", "status": "active"})
    goal = SavingsGoal.objects.get(owner=user)
    auth_client.post(reverse("finance:contribution_create", args=[goal.pk]), {"date": t.isoformat(), "amount": "100"})
    auth_client.post(reverse("finance:contribution_create", args=[goal.pk]), {"date": t.isoformat(), "amount": "50", "from_account": cur.pk, "to_account": sav.pk})
    assert fin.goal_current_amount(goal) == D("150.00") and fin.account_balances(user)[cur.pk] == D("450.00")
    html = auth_client.get(reverse("finance:savings_detail", args=[goal.pk])).content.decode()
    assert "£150.00" in html and "Record only" in html and "Cur → Sav" in html and "Needed per month" in html
    r = auth_client.post(reverse("finance:contribution_create", args=[goal.pk]), {"date": t.isoformat(), "amount": "5", "from_account": cur.pk})
    assert r.status_code == 200 and "both accounts" in r.content.decode()
    assert fin.goal_current_amount(goal) == D("150.00")
    confirm = auth_client.get(reverse("finance:savings_delete", args=[goal.pk])).content.decode()
    assert "1 linked account transfer will also be removed" in confirm
    auth_client.post(reverse("finance:savings_delete", args=[goal.pk]))
    assert not SavingsGoal.objects.exists() and fin.account_balances(user)[cur.pk] == D("500.00")


def test_contribution_edit_and_delete_views(auth_client, user, make_account, make_savings):
    cur, sav, goal = make_account(user, "Cur", "500"), make_account(user, "Sav"), make_savings(user)
    c = fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=local_today(user), amount=D("100")), cur, sav)
    auth_client.post(reverse("finance:contribution_update", args=[c.pk]), {"date": local_today(user).isoformat(), "amount": "60", "from_account": cur.pk, "to_account": sav.pk})
    assert fin.account_balances(user)[cur.pk] == D("440.00") and Transaction.objects.count() == 1
    assert "linked account transfer will also be removed" in auth_client.get(reverse("finance:contribution_delete", args=[c.pk])).content.decode()
    auth_client.post(reverse("finance:contribution_delete", args=[c.pk]))
    assert fin.account_balances(user)[cur.pk] == D("500.00") and not Transaction.objects.exists()


def test_savings_validation_and_overdue_message(auth_client, user, make_savings):
    assert auth_client.post(reverse("finance:savings_create"), {"name": "x", "currency": "GBP", "target_amount": "0", "priority": "low", "status": "active"}).status_code == 200
    g = make_savings(user, target="500", target_date=local_today(user) - D1)
    assert "target date has passed" in auth_client.get(reverse("finance:savings_detail", args=[g.pk])).content.decode()
    assert "Target date passed" in auth_client.get(reverse("finance:savings")).content.decode()


# ------------------------------------------------------------- dashboard
def test_dashboard_finance_totals_match_records(auth_client, user, make_account, make_tx, make_debt, make_savings, cat):
    t = local_today(user)
    a = make_account(user, opening="5000"); sav = make_account(user, "Sav")
    debt = make_debt(user, original="1000", due_day=t.day, minimum_payment=D("75"))
    make_tx(user, T.INCOME, "3000.00", a); make_tx(user, T.EXPENSE, "400.00", a, category=cat(user, "Groceries"))
    make_tx(user, T.TRANSFER, "700.00", a, destination_account=sav)
    make_tx(user, T.DEBT_PAYMENT, "250.00", a, debt=debt, principal_amount="230.00", interest_amount="20.00")
    summary = fin.month_summary(user, "GBP", t.year, t.month)
    assert summary == fin.dashboard_finance(user, t)["summary"]
    assert (summary["income"], summary["spending"], summary["debt_paid"], summary["net_cash_flow"]) == (D("3000.00"), D("420.00"), D("250.00"), D("2350.00"))
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    for text in ("£3,000.00", "£420.00", "£250.00", "£2,350.00", "£770.00", "Upcoming payments", "£75.00"):     # 770 = 1000 - 230
        assert text in html, text
    assert "£700.00" not in html                                                         # the transfer appears nowhere as spending/income


@pytest.mark.parametrize("due_day,today,expected", [
    (15, dt.date(2026, 3, 10), dt.date(2026, 3, 15)), (15, dt.date(2026, 3, 15), dt.date(2026, 3, 15)), (15, dt.date(2026, 3, 16), dt.date(2026, 4, 15)),
    (31, dt.date(2026, 2, 10), dt.date(2026, 2, 28)), (31, dt.date(2028, 2, 10), dt.date(2028, 2, 29)), (31, dt.date(2026, 4, 1), dt.date(2026, 4, 30)),
    (5, dt.date(2026, 12, 20), dt.date(2027, 1, 5)),
])
def test_next_due_date(due_day, today, expected):
    assert fin.next_due_date(due_day, today) == expected


def test_upcoming_bills_window_and_status(user, make_debt):
    t = local_today(user)
    soon = make_debt(user, "Soon", due_day=min(28, (t + dt.timedelta(days=3)).day), minimum_payment=D("40"))
    make_debt(user, "NoMin", due_day=t.day, minimum_payment=D("0"))
    make_debt(user, "Closed", due_day=t.day, minimum_payment=D("10"), status="closed")
    make_debt(user, "NoDay", minimum_payment=D("10"))
    names = [b["debt"].creditor for b in fin.upcoming_bills(user, t, days=14)]
    assert "Soon" in names and not {"NoMin", "Closed", "NoDay"} & set(names)


def test_overview_numbers_and_chart_data_match_records(auth_client, user, make_account, make_tx):
    a = make_account(user, opening="100"); t = local_today(user)
    make_tx(user, T.INCOME, "50", a); make_tx(user, T.EXPENSE, "20", a)
    r = auth_client.get(reverse("finance:overview"))
    html = r.content.decode()
    assert "£50.00" in html and "£20.00" in html and "£130.00" in html                    # income, spending, account balance
    data = json.loads(html.split('id="chart-data" type="application/json">')[1].split("</script>")[0])
    assert data["income"][-1] == 50.0 and data["spending"][-1] == 20.0 and len(data["labels"]) == 6


def test_overview_query_count_is_flat(auth_client, user, make_account, make_tx):
    a = make_account(user, opening="100")
    make_tx(user, T.INCOME, "5", a)
    auth_client.get(reverse("finance:overview"))
    with CaptureQueriesContext(connection) as small:
        auth_client.get(reverse("finance:overview"))
    for i in range(20):
        acc = make_account(user, f"A{i}")
        make_tx(user, T.EXPENSE, "1", acc)
    with CaptureQueriesContext(connection) as large:
        auth_client.get(reverse("finance:overview"))
    assert len(large) <= len(small) + 2 and len(large) < 40


# -------------------------------------------------------------------- CSV
def test_csv_export_content_filters_and_ownership(auth_client, user, other_user, make_account, make_tx):
    a, theirs = make_account(user, "Main", "100"), make_account(other_user, "Theirs", "100")
    make_tx(user, T.EXPENSE, "12.50", a, description="Lunch"); make_tx(user, T.INCOME, "40", a, description="Gift")
    make_tx(other_user, T.INCOME, "999", theirs, description="Secret")
    r = auth_client.get(reverse("finance:transactions_export"))
    assert r["Content-Type"].startswith("text/csv") and "attachment" in r["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.content.decode())))
    assert rows[0][:4] == ["date", "type", "amount", "currency"] and len(rows) == 3 and "Secret" not in r.content.decode()
    only = list(csv.reader(io.StringIO(auth_client.get(reverse("finance:transactions_export"), {"type": "income"}).content.decode())))
    assert len(only) == 2 and only[1][7] == "Gift"


@pytest.mark.parametrize("payload", ["=HYPERLINK(\"http://evil\")", "+1+1", "@SUM(A1)", "-2+3"])
def test_csv_export_neutralises_formula_injection(auth_client, user, make_account, make_tx, payload):
    a = make_account(user, "Main")
    make_tx(user, T.EXPENSE, "1", a, description=payload)
    row = list(csv.reader(io.StringIO(auth_client.get(reverse("finance:transactions_export")).content.decode())))[1]
    assert row[7].startswith("'") and row[7][1:] == payload


def test_csv_export_keeps_negative_looking_numbers_but_not_text(auth_client, user, make_account, make_tx):
    from apps.finance.views import _csv_safe
    assert _csv_safe("-12.5") == "-12.5" and _csv_safe("Hello") == "Hello" and _csv_safe(None) == "" and _csv_safe("=1") == "'=1"


# ------------------------------------------------------ export + deletion
def test_data_export_contains_only_own_finance_data(auth_client, user, other_user, make_account, make_tx, make_debt, make_savings):
    a = make_account(user, "Mine", "10.50"); make_tx(user, T.INCOME, "5", a, description="Mine tx"); make_debt(user, "MyBank"); make_savings(user, "MyGoal")
    b = make_account(other_user, "SecretAcct"); make_tx(other_user, T.INCOME, "5", b, description="SecretTx"); make_debt(other_user, "SecretBank")
    r = auth_client.get(reverse("accounts:export"))
    data = json.loads(r.content)["finance"]
    assert [x["name"] for x in data["accounts"]] == ["Mine"] and data["accounts"][0]["opening_balance"] == "10.50"
    assert data["transactions"][0]["description"] == "Mine tx" and data["debts"][0]["creditor"] == "MyBank" and data["savings_goals"][0]["name"] == "MyGoal"
    assert "Secret" not in r.content.decode()


def test_deleting_account_removes_all_finance_data_despite_protected_relations(client, user, other_user, password, make_account, make_tx, make_debt, make_savings):
    cur, sav, debt, goal = make_account(user, "Cur", "500"), make_account(user, "Sav"), make_debt(user), make_savings(user)
    make_tx(user, T.DEBT_PAYMENT, "50", cur, debt=debt, principal_amount="40", interest_amount="10")
    fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=local_today(user), amount=D("25")), cur, sav)
    Budget.objects.create(owner=user, category=Category.objects.get(owner=user, name="Groceries"), year=2026, month=1, currency="GBP", limit_amount=D("5"))
    keep = make_account(other_user, "Keep", "7")
    client.force_login(user)
    r = client.post(reverse("accounts:delete"), {"password": password, "confirm": "on"})
    assert r.status_code == 302
    for model in (Account, Transaction, Debt, SavingsGoal, SavingsContribution, Budget, Category, DebtDrawdown):
        assert not model.objects.filter(owner_id=user.pk).exists(), model
    assert Account.objects.filter(pk=keep.pk).exists() and Category.objects.filter(owner=other_user).exists()
