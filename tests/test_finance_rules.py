"""Financial integrity rules from the spec (§10, §17, §18)."""
import datetime as dt
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError

from apps.core.services import local_today
from apps.finance import services as fin
from apps.finance.models import Account, Budget, Category, Debt, DebtDrawdown, SavingsContribution, Transaction

pytestmark = pytest.mark.django_db
T = Transaction.Type
TODAY = local_today  # alias


def bal(user, account):
    return fin.account_balances(user)[account.pk]


# ================= balances =================
def test_opening_balance_is_the_starting_point(user, make_account):
    assert bal(user, make_account(user, opening="250.50")) == D("250.50")


def test_income_increases_balance(user, make_account, make_tx):
    a = make_account(user, opening="100")
    make_tx(user, T.INCOME, "40.25", a)
    assert bal(user, a) == D("140.25")


def test_expense_reduces_balance(user, make_account, make_tx):
    a = make_account(user, opening="100")
    make_tx(user, T.EXPENSE, "30.10", a)
    assert bal(user, a) == D("69.90")


def test_transfer_moves_money_between_accounts(user, make_account, make_tx):
    cur, sav = make_account(user, "Current", "500"), make_account(user, "Savings", "0", account_type="savings")
    make_tx(user, T.TRANSFER, "200", cur, destination_account=sav)
    assert bal(user, cur) == D("300.00") and bal(user, sav) == D("200.00")
    assert bal(user, cur) + bal(user, sav) == D("500.00")          # money is conserved


def test_transfer_is_not_income_or_spending(user, make_account, make_tx):
    cur, sav = make_account(user, "Current", "500"), make_account(user, "Savings")
    make_tx(user, T.TRANSFER, "200", cur, destination_account=sav)
    s = fin.month_summary(user, "GBP", TODAY(user).year, TODAY(user).month)
    assert (s["income"], s["spending"], s["debt_paid"], s["net_cash_flow"]) == (D("0"), D("0"), D("0"), D("0"))
    first, nxt = fin.month_bounds(TODAY(user).year, TODAY(user).month)
    assert fin.category_breakdown(user, "GBP", first, nxt) == []


def test_credit_card_goes_negative_and_payment_via_transfer_clears_it(user, make_account, make_tx):
    cur, card = make_account(user, "Current", "1000"), make_account(user, "Card", "0", account_type="credit_card")
    make_tx(user, T.EXPENSE, "120", card)
    assert bal(user, card) == D("-120.00")
    make_tx(user, T.TRANSFER, "120", cur, destination_account=card)
    assert bal(user, card) == D("0.00") and bal(user, cur) == D("880.00")


def test_balances_are_isolated_between_users(user, other_user, make_account, make_tx):
    a, b = make_account(user, "A", "10"), make_account(other_user, "B", "10")
    make_tx(other_user, T.INCOME, "999", b)
    assert bal(user, a) == D("10.00") and b.pk not in fin.account_balances(user)


def test_deleting_a_transaction_restores_balance_exactly_once(user, make_account, make_tx):
    a = make_account(user, opening="100")
    tx = make_tx(user, T.EXPENSE, "40", a)
    fin.delete_transaction(tx)
    assert bal(user, a) == D("100.00")


def test_editing_a_transaction_does_not_double_count(user, make_account, make_tx):
    a = make_account(user, opening="100")
    tx = make_tx(user, T.EXPENSE, "40", a)
    tx.amount = D("25.00")
    fin.save_transaction(tx)
    assert bal(user, a) == D("75.00")
    other = make_account(user, "Other", "0")
    tx.account = other
    fin.save_transaction(tx)
    assert bal(user, a) == D("100.00") and bal(user, other) == D("-25.00")


def test_decimal_precision_no_float_drift(user, make_account, make_tx):
    a = make_account(user)
    for _ in range(10):
        make_tx(user, T.INCOME, "0.10", a)
    assert bal(user, a) == D("1.00")                                 # floats would give 0.9999999999999999


# ================= validation =================
@pytest.mark.parametrize("amount", ["0", "-5", "-0.01"])
def test_non_positive_amounts_rejected(user, make_account, make_tx, amount):
    with pytest.raises(ValidationError):
        make_tx(user, T.EXPENSE, amount, make_account(user))


def test_too_many_decimal_places_rejected_by_field_validation(user, make_account):
    tx = Transaction(owner=user, type=T.INCOME, amount=D("1.234"), account=make_account(user), date=TODAY(user))
    with pytest.raises(ValidationError):
        tx.full_clean()


def test_transfer_requires_distinct_accounts(user, make_account, make_tx):
    a = make_account(user)
    with pytest.raises(ValidationError):
        make_tx(user, T.TRANSFER, "10", a, destination_account=a)
    with pytest.raises(ValidationError):
        make_tx(user, T.TRANSFER, "10", a)


def test_transfer_needs_same_owner_and_same_currency(user, other_user, make_account, make_tx):
    a, theirs, usd = make_account(user, "A"), make_account(other_user, "T"), make_account(user, "USD", currency="USD")
    with pytest.raises(ValidationError):
        make_tx(user, T.TRANSFER, "10", a, destination_account=theirs)
    with pytest.raises(ValidationError, match="different currencies"):
        make_tx(user, T.TRANSFER, "10", a, destination_account=usd)


def test_cannot_use_another_users_account_category_or_debt(user, other_user, make_account, make_tx, make_debt, cat):
    mine, theirs = make_account(user, "Mine"), make_account(other_user, "Theirs")
    with pytest.raises(ValidationError):
        make_tx(user, T.INCOME, "10", theirs)
    with pytest.raises(ValidationError):
        make_tx(user, T.EXPENSE, "10", mine, category=cat(other_user, "Groceries"))
    with pytest.raises(ValidationError):
        make_tx(user, T.DEBT_PAYMENT, "10", mine, debt=make_debt(other_user), principal_amount="10", interest_amount="0")


def test_category_kind_must_match_type(user, make_account, make_tx, cat):
    a = make_account(user)
    with pytest.raises(ValidationError):
        make_tx(user, T.INCOME, "10", a, category=cat(user, "Groceries"))
    with pytest.raises(ValidationError):
        make_tx(user, T.EXPENSE, "10", a, category=cat(user, "Salary", "income"))


def test_shape_rules_per_type(user, make_account, make_tx, make_debt):
    a, b = make_account(user, "A"), make_account(user, "B")
    with pytest.raises(ValidationError):
        make_tx(user, T.EXPENSE, "10", a, destination_account=b)
    with pytest.raises(ValidationError):
        make_tx(user, T.INCOME, "10", a, debt=make_debt(user))
    with pytest.raises(ValidationError):
        make_tx(user, T.EXPENSE, "10", a, principal_amount="5")
    with pytest.raises(ValidationError):
        make_tx(user, T.DEBT_PAYMENT, "10", a)                          # no debt


def test_database_constraints_backstop_the_app_rules(user, make_account, make_tx, make_debt):
    a, b = make_account(user, "A"), make_account(user, "B")
    day = TODAY(user)
    bad = [
        dict(type=T.INCOME, amount=D("0"), account=a),                                   # amount > 0
        dict(type=T.TRANSFER, amount=D("5"), account=a, destination_account=a),           # self transfer
        dict(type=T.TRANSFER, amount=D("5"), account=a),                                  # no destination
        dict(type=T.EXPENSE, amount=D("5"), account=a, destination_account=b),            # income/expense shape
        dict(type=T.DEBT_PAYMENT, amount=D("10"), account=a, debt=make_debt(user), principal_amount=D("4"), interest_amount=D("4")),  # parts != total
        dict(type=T.DEBT_PAYMENT, amount=D("10"), account=a),                              # no debt
        dict(type=T.INCOME, amount=D("10"), account=a, principal_amount=D("1")),           # parts only for debt payments
    ]
    for kw in bad:
        with pytest.raises(IntegrityError), transaction.atomic():
            Transaction.objects.bulk_create([Transaction(owner=user, date=day, **kw)])


def test_account_and_category_names_unique_case_insensitively(user, make_account):
    make_account(user, "Main")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_account(user, "MAIN")
    with pytest.raises(IntegrityError), transaction.atomic():
        Category.objects.create(owner=user, name="GROCERIES", kind="expense")


def test_default_categories_seeded_and_interest_category_is_system(user):
    names = set(Category.objects.filter(owner=user, kind="expense").values_list("name", flat=True))
    assert {"Groceries", "Transport", "Debt interest"} <= names
    assert Category.objects.get(owner=user, name="Debt interest").is_system
    assert Category.objects.filter(owner=user, kind="income", name="Salary").exists()


# ================= debts =================
def pay(user, make_tx, account, debt, principal, interest, **kw):
    total = D(str(principal)) + D(str(interest))
    return make_tx(user, T.DEBT_PAYMENT, total, account, debt=debt, principal_amount=principal, interest_amount=interest, **kw)


def test_debt_payment_total_leaves_account_once(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user, original="1000")
    pay(user, make_tx, a, debt, "90", "10")
    assert bal(user, a) == D("900.00")                     # 100 out, not 90, not 110


def test_principal_reduces_outstanding_interest_does_not(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user, original="1000")
    assert fin.outstanding_principal(debt) == D("1000.00")
    pay(user, make_tx, a, debt, "90", "10")
    assert fin.outstanding_principal(debt) == D("910.00")
    pay(user, make_tx, a, debt, "0", "25")                  # interest-only payment
    assert fin.outstanding_principal(debt) == D("910.00")


def test_interest_counts_as_spending_once_principal_never(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user)
    make_tx(user, T.EXPENSE, "50", a)
    pay(user, make_tx, a, debt, "90", "10")
    t = TODAY(user)
    s = fin.month_summary(user, "GBP", t.year, t.month)
    assert s["spending"] == D("60.00")                      # 50 expense + 10 interest (not +90 principal, not 10 twice)
    assert (s["ordinary_spending"], s["interest_paid"], s["principal_repaid"], s["debt_paid"]) == (D("50"), D("10"), D("90"), D("100"))
    assert s["net_cash_flow"] == D("-150.00")               # 0 income - 50 - 100 cash out


def test_interest_reported_under_debt_interest_category_only(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user)
    tx = pay(user, make_tx, a, debt, "90", "10")
    assert tx.category.name == "Debt interest"
    t = TODAY(user); first, nxt = fin.month_bounds(t.year, t.month)
    rows = fin.category_breakdown(user, "GBP", first, nxt)
    assert [(r["name"], r["total"]) for r in rows] == [("Debt interest", D("10.00"))]
    tx2 = pay(user, make_tx, a, debt, "50", "0")
    assert tx2.category is None


def test_payment_must_reconcile(user, make_account, make_tx, make_debt):
    a, debt = make_account(user), make_debt(user)
    with pytest.raises(ValidationError, match="must equal the total"):
        make_tx(user, T.DEBT_PAYMENT, "100", a, debt=debt, principal_amount="80", interest_amount="10")


def test_cannot_repay_more_principal_than_outstanding(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="5000"), make_debt(user, original="100")
    with pytest.raises(ValidationError, match="outstanding principal"):
        pay(user, make_tx, a, debt, "100.01", "0")
    pay(user, make_tx, a, debt, "100", "0")
    debt.refresh_from_db()
    assert debt.status == Debt.Status.PAID_OFF


def test_payment_currency_must_match_debt(user, make_account, make_tx, make_debt):
    usd, debt = make_account(user, currency="USD"), make_debt(user, currency="GBP")
    with pytest.raises(ValidationError, match="same currency"):
        pay(user, make_tx, usd, debt, "10", "0")


def test_editing_a_payment_recalculates_outstanding(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user, original="500")
    tx = pay(user, make_tx, a, debt, "100", "5")
    tx.principal_amount, tx.interest_amount, tx.amount = D("150"), D("0"), D("150")
    fin.save_transaction(tx)
    assert fin.outstanding_principal(debt) == D("350.00") and bal(user, a) == D("850.00")
    assert fin.month_summary(user, "GBP", TODAY(user).year, TODAY(user).month)["interest_paid"] == D("0.00")
    tx.refresh_from_db(); assert tx.category is None            # no interest -> no interest category


def test_editing_payment_may_use_its_own_principal_again(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user, original="100")
    tx = pay(user, make_tx, a, debt, "100", "0")                   # fully paid
    tx.principal_amount = tx.amount = D("90")
    fin.save_transaction(tx)                                       # must not count its own 100 as already repaid
    debt.refresh_from_db()
    assert fin.outstanding_principal(debt) == D("10.00") and debt.status == Debt.Status.ACTIVE


def test_deleting_payment_restores_debt_and_reopens_it(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user, original="100")
    tx = pay(user, make_tx, a, debt, "100", "0")
    fin.delete_transaction(tx)
    debt.refresh_from_db()
    assert fin.outstanding_principal(debt) == D("100.00") and debt.status == Debt.Status.ACTIVE and bal(user, a) == D("1000.00")


def test_drawdown_increases_outstanding_and_reopens_paid_debt(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user, original="100")
    pay(user, make_tx, a, debt, "100", "0")
    fin.save_drawdown(DebtDrawdown(owner=user, debt=debt, date=TODAY(user), amount=D("50")))
    debt.refresh_from_db()
    assert fin.outstanding_principal(debt) == D("50.00") and debt.status == Debt.Status.ACTIVE


def test_cannot_delete_drawdown_that_repaid_principal_depends_on(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user, original="100")
    dd = DebtDrawdown.objects.create(owner=user, debt=debt, date=TODAY(user), amount=D("50"))
    pay(user, make_tx, a, debt, "140", "0")
    with pytest.raises(ValidationError):
        fin.delete_drawdown(dd)
    assert DebtDrawdown.objects.filter(pk=dd.pk).exists()


def test_cannot_lower_original_below_repaid(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user, original="100")
    pay(user, make_tx, a, debt, "80", "0")
    debt.original_amount = D("50")
    with pytest.raises(ValidationError):
        fin.save_debt(debt)


def test_debt_and_account_are_restricted_from_deletion_while_in_use(user, make_account, make_tx, make_debt):
    a, debt = make_account(user, opening="1000"), make_debt(user)
    pay(user, make_tx, a, debt, "10", "0")
    with pytest.raises(RestrictedError):
        debt.delete()
    with pytest.raises(RestrictedError):
        a.delete()


def test_debts_with_balances_matches_single_debt_calc(user, make_account, make_tx, make_debt):
    a = make_account(user, opening="1000")
    d1, d2 = make_debt(user, "One", original="300"), make_debt(user, "Two", original="200")
    pay(user, make_tx, a, d1, "100", "5"); pay(user, make_tx, a, d1, "20", "0"); pay(user, make_tx, a, d2, "50", "1")
    DebtDrawdown.objects.create(owner=user, debt=d2, date=TODAY(user), amount=D("25"))
    by = {d.creditor: d for d in fin.debts_with_balances(user)}
    assert by["One"].outstanding == fin.outstanding_principal(d1) == D("180.00")
    assert by["Two"].outstanding == fin.outstanding_principal(d2) == D("175.00")


# ================= savings goals =================
def test_earmark_contribution_has_no_balance_or_spending_effect(user, make_account, make_savings):
    a, goal = make_account(user, opening="500"), make_savings(user)
    c = SavingsContribution(owner=user, goal=goal, date=TODAY(user), amount=D("100"))
    fin.save_contribution(c)
    t = TODAY(user)
    assert bal(user, a) == D("500.00") and fin.goal_current_amount(goal) == D("100.00")
    assert fin.month_summary(user, "GBP", t.year, t.month)["spending"] == D("0.00")
    assert c.transfer is None and not Transaction.objects.exists()


def test_linked_contribution_creates_transfer_not_expense(user, make_account, make_savings):
    cur, sav, goal = make_account(user, "Current", "500"), make_account(user, "Savings", account_type="savings"), make_savings(user)
    c = fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=TODAY(user), amount=D("150")), cur, sav)
    assert c.transfer.type == T.TRANSFER and c.transfer.amount == D("150.00")
    assert bal(user, cur) == D("350.00") and bal(user, sav) == D("150.00")
    t = TODAY(user); s = fin.month_summary(user, "GBP", t.year, t.month)
    assert (s["spending"], s["income"], s["net_cash_flow"]) == (D("0"), D("0"), D("0"))


def test_contribution_accounts_must_be_both_or_neither(user, make_account, make_savings):
    a, goal = make_account(user), make_savings(user)
    with pytest.raises(ValidationError):
        fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=TODAY(user), amount=D("10")), a, None)


def test_contribution_currency_must_match_goal(user, make_account, make_savings):
    usd1, usd2, goal = make_account(user, "U1", currency="USD"), make_account(user, "U2", currency="USD"), make_savings(user, currency="GBP")
    with pytest.raises(ValidationError, match="currency"):
        fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=TODAY(user), amount=D("10")), usd1, usd2)
    assert not Transaction.objects.exists() and not SavingsContribution.objects.exists()      # atomic: nothing half-saved


def test_editing_linked_contribution_updates_transfer_consistently(user, make_account, make_savings):
    cur, sav, goal = make_account(user, "Current", "500"), make_account(user, "Savings"), make_savings(user)
    c = fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=TODAY(user), amount=D("100")), cur, sav)
    c.amount = D("40")
    fin.save_contribution(c, cur, sav)
    assert Transaction.objects.count() == 1                                   # updated, not duplicated
    assert bal(user, cur) == D("460.00") and bal(user, sav) == D("40.00") and fin.goal_current_amount(goal) == D("40.00")


def test_switching_linked_contribution_to_earmark_removes_transfer(user, make_account, make_savings):
    cur, sav, goal = make_account(user, "Current", "500"), make_account(user, "Savings"), make_savings(user)
    c = fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=TODAY(user), amount=D("100")), cur, sav)
    fin.save_contribution(c)
    assert not Transaction.objects.exists() and bal(user, cur) == D("500.00") and fin.goal_current_amount(goal) == D("100.00")


def test_deleting_contribution_removes_its_transfer(user, make_account, make_savings):
    cur, sav, goal = make_account(user, "Current", "500"), make_account(user, "Savings"), make_savings(user)
    c = fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=TODAY(user), amount=D("100")), cur, sav)
    fin.delete_contribution(c)
    assert not Transaction.objects.exists() and bal(user, cur) == D("500.00") and bal(user, sav) == D("0.00")


def test_deleting_goal_removes_contributions_and_linked_transfers_only(user, make_account, make_savings, make_tx):
    cur, sav = make_account(user, "Current", "500"), make_account(user, "Savings")
    g1, g2 = make_savings(user, "G1"), make_savings(user, "G2")
    fin.save_contribution(SavingsContribution(owner=user, goal=g1, date=TODAY(user), amount=D("100")), cur, sav)
    fin.save_contribution(SavingsContribution(owner=user, goal=g2, date=TODAY(user), amount=D("30")), cur, sav)
    make_tx(user, T.EXPENSE, "10", cur)
    fin.delete_savings_goal(g1)
    assert bal(user, cur) == D("460.00") and bal(user, sav) == D("30.00")      # only G1's transfer undone; expense and G2 intact
    assert SavingsContribution.objects.filter(goal=g2).exists()


def test_linked_transfer_cannot_be_edited_or_deleted_directly(user, make_account, make_savings):
    cur, sav, goal = make_account(user, "Current", "500"), make_account(user, "Savings"), make_savings(user)
    c = fin.save_contribution(SavingsContribution(owner=user, goal=goal, date=TODAY(user), amount=D("100")), cur, sav)
    tx = Transaction.objects.get(pk=c.transfer_id)
    with pytest.raises(ValidationError):
        fin.delete_transaction(tx)
    tx.amount = D("1")
    with pytest.raises(ValidationError):
        fin.save_transaction(tx)
    assert Transaction.objects.get(pk=tx.pk).amount == D("100.00")


def test_goal_progress_info_and_monthly_estimate(user, make_savings):
    t = TODAY(user)
    goal = make_savings(user, target="1200", target_date=t + dt.timedelta(days=365))
    SavingsContribution.objects.create(owner=user, goal=goal, date=t, amount=D("300"))
    info = fin.goal_progress_info(goal, t)
    assert info["current"] == D("300.00") and info["pct"] == 25 and info["plan"]["remaining"] == D("900.00")
    assert info["plan"]["state"] == "ok" and info["plan"]["monthly"] > 0
    listed = fin.goals_with_progress(user, t)[0]
    assert listed.current == D("300.00") and listed.pct == 25


def test_overdue_and_achieved_goal_states(user, make_savings):
    t = TODAY(user)
    late = make_savings(user, "Late", target="500", target_date=t - dt.timedelta(days=1))
    done = make_savings(user, "Done", target="100", target_date=t + dt.timedelta(days=30))
    SavingsContribution.objects.create(owner=user, goal=done, date=t, amount=D("120"))
    by = {g.name: g for g in fin.goals_with_progress(user, t)}
    assert by["Late"].plan["state"] == "overdue" and by["Done"].plan["state"] == "achieved" and by["Done"].pct == 100


# ================= budgets =================
def test_budget_status_thresholds():
    assert fin.budget_status(D("0"), D("100"))["state"] == "ok"
    assert fin.budget_status(D("79.99"), D("100"))["state"] == "ok"
    assert fin.budget_status(D("80"), D("100"))["state"] == "warning"
    assert fin.budget_status(D("100"), D("100"))["state"] == "warning"
    over = fin.budget_status(D("120.50"), D("100"))
    assert over["state"] == "over" and over["remaining"] == D("-20.50") and over["pct"] == 100


def test_budget_counts_only_matching_category_and_month(user, make_account, make_tx, cat):
    a, groceries, transport = make_account(user, opening="1000"), cat(user, "Groceries"), cat(user, "Transport")
    Budget.objects.create(owner=user, category=groceries, year=2026, month=3, currency="GBP", limit_amount=D("200"))
    make_tx(user, T.EXPENSE, "50", a, date=dt.date(2026, 3, 1), category=groceries)
    make_tx(user, T.EXPENSE, "70", a, date=dt.date(2026, 3, 31), category=groceries)
    make_tx(user, T.EXPENSE, "999", a, date=dt.date(2026, 2, 28), category=groceries)     # previous month
    make_tx(user, T.EXPENSE, "999", a, date=dt.date(2026, 4, 1), category=groceries)      # next month
    make_tx(user, T.EXPENSE, "999", a, date=dt.date(2026, 3, 15), category=transport)     # other category
    row = fin.budgets_for_month(user, 2026, 3)[0]
    assert (row["spent"], row["remaining"], row["pct"], row["state"]) == (D("120.00"), D("80.00"), 60, "ok")


def test_budget_december_january_boundary(user, make_account, make_tx, cat):
    a, c = make_account(user, opening="1000"), cat(user, "Groceries")
    Budget.objects.create(owner=user, category=c, year=2026, month=12, currency="GBP", limit_amount=D("100"))
    make_tx(user, T.EXPENSE, "10", a, date=dt.date(2026, 12, 31), category=c)
    make_tx(user, T.EXPENSE, "50", a, date=dt.date(2027, 1, 1), category=c)
    assert fin.budgets_for_month(user, 2026, 12)[0]["spent"] == D("10.00")


def test_budget_excludes_transfers_and_principal_but_includes_interest(user, make_account, make_tx, make_debt):
    cur, sav, debt = make_account(user, "Cur", "5000"), make_account(user, "Sav"), make_debt(user, original="1000")
    interest = Category.objects.get(owner=user, name="Debt interest")
    t = TODAY(user)
    Budget.objects.create(owner=user, category=interest, year=t.year, month=t.month, currency="GBP", limit_amount=D("20"))
    make_tx(user, T.TRANSFER, "700", cur, destination_account=sav)
    pay(user, make_tx, cur, debt, "300", "18")
    row = fin.budgets_for_month(user, t.year, t.month)[0]
    assert row["spent"] == D("18.00") and row["state"] == "warning"           # 90% of 20; principal 300 and transfer 700 ignored


def test_budget_ignores_other_currency_spending(user, make_account, make_tx, cat):
    gbp, usd, c = make_account(user, "G", "100"), make_account(user, "U", "100", currency="USD"), cat(user, "Groceries")
    t = TODAY(user)
    Budget.objects.create(owner=user, category=c, year=t.year, month=t.month, currency="GBP", limit_amount=D("100"))
    make_tx(user, T.EXPENSE, "40", gbp, category=c); make_tx(user, T.EXPENSE, "500", usd, category=c)
    assert fin.budgets_for_month(user, t.year, t.month)[0]["spent"] == D("40.00")


def test_budget_constraints(user, cat):
    c = cat(user, "Groceries")
    Budget.objects.create(owner=user, category=c, year=2026, month=1, currency="GBP", limit_amount=D("10"))
    with pytest.raises(IntegrityError), transaction.atomic():
        Budget.objects.create(owner=user, category=c, year=2026, month=1, currency="GBP", limit_amount=D("20"))
    with pytest.raises(IntegrityError), transaction.atomic():
        Budget.objects.create(owner=user, category=c, year=2026, month=2, currency="GBP", limit_amount=D("0"))
    with pytest.raises(IntegrityError), transaction.atomic():
        Budget.objects.create(owner=user, category=c, year=2026, month=13, currency="GBP", limit_amount=D("5"))
    with pytest.raises(ValidationError):
        Budget(owner=user, category=cat(user, "Salary", "income"), year=2026, month=3, currency="GBP", limit_amount=D("5")).full_clean()


# ================= reports =================
def test_month_summary_totals_and_boundaries(user, make_account, make_tx):
    a = make_account(user, opening="0")
    make_tx(user, T.INCOME, "2000", a, date=dt.date(2026, 3, 1))
    make_tx(user, T.EXPENSE, "300", a, date=dt.date(2026, 3, 31))
    make_tx(user, T.INCOME, "5000", a, date=dt.date(2026, 4, 1))        # excluded
    make_tx(user, T.EXPENSE, "700", a, date=dt.date(2026, 2, 28))       # excluded
    s = fin.month_summary(user, "GBP", 2026, 3)
    assert (s["income"], s["spending"], s["net_cash_flow"]) == (D("2000.00"), D("300.00"), D("1700.00"))


def test_feb_leap_and_non_leap_boundaries(user, make_account, make_tx):
    a = make_account(user)
    make_tx(user, T.EXPENSE, "1", a, date=dt.date(2028, 2, 29)); make_tx(user, T.EXPENSE, "10", a, date=dt.date(2028, 3, 1))
    assert fin.month_summary(user, "GBP", 2028, 2)["spending"] == D("1.00")
    make_tx(user, T.EXPENSE, "100", a, date=dt.date(2027, 2, 28)); make_tx(user, T.EXPENSE, "1000", a, date=dt.date(2027, 3, 1))
    assert fin.month_summary(user, "GBP", 2027, 2)["spending"] == D("100.00")


def test_reports_never_mix_currencies(user, make_account, make_tx):
    gbp, usd = make_account(user, "G"), make_account(user, "U", currency="USD")
    make_tx(user, T.INCOME, "100", gbp, date=dt.date(2026, 3, 5)); make_tx(user, T.INCOME, "900", usd, date=dt.date(2026, 3, 5))
    assert fin.month_summary(user, "GBP", 2026, 3)["income"] == D("100.00")
    assert fin.month_summary(user, "USD", 2026, 3)["income"] == D("900.00")


def test_category_breakdown_sums_to_total_spending(user, make_account, make_tx, make_debt, cat):
    a, debt = make_account(user, opening="5000"), make_debt(user)
    d = dt.date(2026, 3, 10)
    make_tx(user, T.EXPENSE, "60", a, date=d, category=cat(user, "Groceries"))
    make_tx(user, T.EXPENSE, "30", a, date=d, category=cat(user, "Transport"))
    make_tx(user, T.EXPENSE, "10", a, date=d)                                    # uncategorised
    pay(user, make_tx, a, debt, "200", "20", date=d)
    first, nxt = fin.month_bounds(2026, 3)
    rows = fin.category_breakdown(user, "GBP", first, nxt)
    assert sum(r["total"] for r in rows) == fin.month_summary(user, "GBP", 2026, 3)["spending"] == D("120.00")
    assert [r["name"] for r in rows] == ["Groceries", "Transport", "Debt interest", "Uncategorised"]


def test_monthly_series_and_year_summary(user, make_account, make_tx):
    a = make_account(user)
    make_tx(user, T.INCOME, "100", a, date=dt.date(2026, 11, 20)); make_tx(user, T.EXPENSE, "40", a, date=dt.date(2026, 12, 5))
    make_tx(user, T.INCOME, "50", a, date=dt.date(2027, 1, 3))
    series = fin.monthly_series(user, "GBP", 2027, 1, n=3)
    assert [(s["label"], s["income"], s["spending"]) for s in series] == [
        ("Nov 2026", D("100.00"), D("0.00")), ("Dec 2026", D("0.00"), D("40.00")), ("Jan 2027", D("50.00"), D("0.00"))]
    ys = fin.year_summary(user, "GBP", 2026)
    assert len(ys["months"]) == 12 and ys["total"]["income"] == D("100.00") and ys["total"]["spending"] == D("40.00")
    assert ys["months"][0]["label"] == "Jan 2026" and ys["months"][-1]["label"] == "Dec 2026"


def test_compare_periods_with_and_without_previous_data(user, make_account, make_tx):
    a = make_account(user)
    make_tx(user, T.EXPENSE, "100", a, date=dt.date(2026, 2, 10)); make_tx(user, T.EXPENSE, "150", a, date=dt.date(2026, 3, 10))
    cmp = fin.compare_periods(fin.month_summary(user, "GBP", 2026, 3), fin.month_summary(user, "GBP", 2026, 2))
    assert cmp["spending"]["delta"] == D("50.00") and cmp["spending"]["pct"] == D("50.0")
    assert cmp["income"]["pct"] is None                                       # previous income 0 -> no division by zero
