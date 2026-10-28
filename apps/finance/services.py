"""Finance business logic: balances, reports, budgets, debts and safe writes.

Reporting rules (single source of truth):
- Balance      = opening + income + transfers in - expenses - transfers out - debt payments (total).
- Spending     = expenses + INTEREST portion of debt payments. Transfers, principal
                 repayments and savings contributions are never spending.
- Net cash flow = income - expenses - total debt payments (principal + interest), i.e. real
                 cash in minus real cash out, excluding internal transfers.
- Reports are per currency; amounts in different currencies are never added together.
"""
import calendar
import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Case, DecimalField, F, Q, Subquery, OuterRef, Sum, Value, When
from django.db.models.functions import Coalesce

from .calculations import ZERO, change_pct, contribution_plan, month_bounds, percent, shift_month
from .models import (
    INTEREST_CATEGORY_NAME, Account, Budget, Category, Debt, DebtDrawdown, SavingsContribution,
    SavingsGoal, Transaction,
)

T = Transaction.Type
MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)
DEFAULT_INCOME = ["Salary", "Freelance", "Gifts", "Interest earned", "Other income"]
DEFAULT_EXPENSE = ["Rent / Mortgage", "Groceries", "Transport", "Utilities", "Eating out", "Health",
                   "Entertainment", "Shopping", "Education", "Travel", "Subscriptions", "Other expenses"]

# What a row contributes to "spending": full amount of an expense, only the interest of a debt payment.
SPEND = Case(
    When(type=T.EXPENSE, then=F("amount")),
    When(type=T.DEBT_PAYMENT, then=F("interest_amount")),
    default=Value(ZERO), output_field=MONEY_FIELD,
)


def _sum(expression):
    return Coalesce(Sum(expression), Value(ZERO), output_field=MONEY_FIELD)


# ---------------------------------------------------------------- categories
def ensure_default_categories(user):
    have = {(c.kind, c.name.lower()) for c in Category.objects.filter(owner=user)}
    rows = [Category(owner=user, kind="income", name=n) for n in DEFAULT_INCOME]
    rows += [Category(owner=user, kind="expense", name=n) for n in DEFAULT_EXPENSE]
    rows.append(Category(owner=user, kind="expense", name=INTEREST_CATEGORY_NAME, is_system=True))
    missing = [c for c in rows if (c.kind, c.name.lower()) not in have]
    if missing:
        Category.objects.bulk_create(missing, ignore_conflicts=True)


def interest_category(user):
    cat, _ = Category.objects.get_or_create(
        owner=user, kind="expense", name__iexact=INTEREST_CATEGORY_NAME,
        defaults={"name": INTEREST_CATEGORY_NAME, "is_system": True})
    return cat


# ------------------------------------------------------------------ balances
def account_balances(user):
    """{account_id: Decimal} for every account of the user, from two grouped queries."""
    accounts = {a.pk: a.opening_balance for a in Account.objects.filter(owner=user)}
    bal = dict(accounts)
    for row in Transaction.objects.filter(owner=user).values("account_id", "type").annotate(total=Sum("amount")):
        sign = 1 if row["type"] == T.INCOME else -1       # expense, transfer-out and debt payment all reduce
        bal[row["account_id"]] += sign * row["total"]
    for row in Transaction.objects.filter(owner=user, type=T.TRANSFER).values("destination_account_id").annotate(total=Sum("amount")):
        bal[row["destination_account_id"]] += row["total"]
    return bal


def accounts_with_balances(user):
    bal = account_balances(user)
    out = list(Account.objects.filter(owner=user))
    for a in out:
        a.balance = bal[a.pk]
    return out


# ------------------------------------------------------------------- reports
def period_summary(user, currency, first, next_first):
    """Totals for [first, next_first) in one currency, one query."""
    agg = Transaction.objects.filter(owner=user, account__currency=currency, date__gte=first, date__lt=next_first).aggregate(
        income=_sum(expression=Case(When(type=T.INCOME, then=F("amount")), default=Value(ZERO), output_field=MONEY_FIELD)),
        expenses=_sum(expression=Case(When(type=T.EXPENSE, then=F("amount")), default=Value(ZERO), output_field=MONEY_FIELD)),
        interest=_sum(expression=Case(When(type=T.DEBT_PAYMENT, then=F("interest_amount")), default=Value(ZERO), output_field=MONEY_FIELD)),
        principal=_sum(expression=Case(When(type=T.DEBT_PAYMENT, then=F("principal_amount")), default=Value(ZERO), output_field=MONEY_FIELD)),
    )
    spending = agg["expenses"] + agg["interest"]
    debt_paid = agg["principal"] + agg["interest"]
    return {
        "income": agg["income"], "ordinary_spending": agg["expenses"], "interest_paid": agg["interest"],
        "spending": spending, "principal_repaid": agg["principal"], "debt_paid": debt_paid,
        "net_cash_flow": agg["income"] - agg["expenses"] - debt_paid,
    }


def month_summary(user, currency, year, month):
    return period_summary(user, currency, *month_bounds(year, month))


def category_breakdown(user, currency, first, next_first):
    rows = (
        Transaction.objects.filter(owner=user, account__currency=currency, date__gte=first, date__lt=next_first, type__in=[T.EXPENSE, T.DEBT_PAYMENT])
        .values("category_id", "category__name").annotate(total=Sum(SPEND)).order_by("-total")
    )
    rows = [r for r in rows if r["total"] and r["total"] > 0]
    grand = sum((r["total"] for r in rows), ZERO)
    return [{"category_id": r["category_id"], "name": r["category__name"] or "Uncategorised", "total": r["total"],
             "pct": percent(r["total"], grand)} for r in rows]


def monthly_series(user, currency, end_year, end_month, n=6):
    """Income and spending for the n months ending at (end_year, end_month), oldest first."""
    start_y, start_m = shift_month(end_year, end_month, -(n - 1))
    first, _ = month_bounds(start_y, start_m)
    _, last = month_bounds(end_year, end_month)
    inc, spend = {}, {}
    qs = Transaction.objects.filter(owner=user, account__currency=currency, date__gte=first, date__lt=last)
    for r in qs.filter(type=T.INCOME).values("date__year", "date__month").annotate(t=Sum("amount")):
        inc[(r["date__year"], r["date__month"])] = r["t"]
    for r in qs.filter(type__in=[T.EXPENSE, T.DEBT_PAYMENT]).values("date__year", "date__month").annotate(t=Sum(SPEND)):
        spend[(r["date__year"], r["date__month"])] = r["t"]
    out = []
    for i in range(n):
        y, m = shift_month(start_y, start_m, i)
        out.append({"year": y, "month": m, "label": dt.date(y, m, 1).strftime("%b %Y"),
                    "income": inc.get((y, m), ZERO), "spending": spend.get((y, m), ZERO)})
    return out


def year_summary(user, currency, year):
    total = period_summary(user, currency, dt.date(year, 1, 1), dt.date(year + 1, 1, 1))
    return {"total": total, "months": monthly_series(user, currency, year, 12, n=12)}


def compare_periods(current, previous):
    """Delta and percentage change per figure; pct is None when the previous value is zero."""
    return {k: {"current": current[k], "previous": previous[k], "delta": current[k] - previous[k],
                "pct": change_pct(current[k], previous[k])} for k in ("income", "spending", "net_cash_flow")}


# ------------------------------------------------------------------- budgets
def budget_status(spent, limit):
    pct = percent(spent, limit)            # rounded, for display only
    # State uses the exact ratio so 79.99% is not mistaken for 80% by rounding.
    state = "over" if spent > limit else ("warning" if spent * 100 >= limit * 80 else "ok")
    return {"spent": spent, "limit": limit, "remaining": limit - spent, "pct": pct, "state": state}


def budgets_for_month(user, year, month):
    budgets = list(Budget.objects.filter(owner=user, year=year, month=month).select_related("category"))
    if not budgets:
        return []
    first, nxt = month_bounds(year, month)
    spent = {}
    rows = (Transaction.objects.filter(owner=user, date__gte=first, date__lt=nxt, type__in=[T.EXPENSE, T.DEBT_PAYMENT],
                                       category_id__in=[b.category_id for b in budgets])
            .values("category_id", "account__currency").annotate(total=Sum(SPEND)))
    for r in rows:
        spent[(r["category_id"], r["account__currency"])] = r["total"]
    return [{"budget": b, **budget_status(spent.get((b.category_id, b.currency), ZERO), b.limit_amount)} for b in budgets]


# --------------------------------------------------------------------- debts
def outstanding_principal(debt, exclude_tx_id=None, exclude_drawdown_id=None):
    """original + drawdowns - principal repaid.

    ASSUMPTION: interest accrual is not modelled. Interest appears only as the
    interest portion you record on each payment, and never changes principal.
    This is a principal tracker, not an amortisation schedule.
    """
    pay = Transaction.objects.filter(debt=debt, type=T.DEBT_PAYMENT)
    if exclude_tx_id:
        pay = pay.exclude(pk=exclude_tx_id)
    draws = DebtDrawdown.objects.filter(debt=debt)
    if exclude_drawdown_id:
        draws = draws.exclude(pk=exclude_drawdown_id)
    repaid = pay.aggregate(t=_sum(expression=F("principal_amount")))["t"]
    drawn = draws.aggregate(t=_sum(expression=F("amount")))["t"]
    return debt.original_amount + drawn - repaid


def debts_with_balances(user):
    repaid = Subquery(Transaction.objects.filter(debt=OuterRef("pk"), type=T.DEBT_PAYMENT).order_by().values("debt")
                      .annotate(s=Sum("principal_amount")).values("s"), output_field=MONEY_FIELD)
    drawn = Subquery(DebtDrawdown.objects.filter(debt=OuterRef("pk")).order_by().values("debt")
                     .annotate(s=Sum("amount")).values("s"), output_field=MONEY_FIELD)
    qs = Debt.objects.filter(owner=user).annotate(repaid=Coalesce(repaid, Value(ZERO)), drawn=Coalesce(drawn, Value(ZERO)))
    out = list(qs)
    for d in out:
        d.outstanding = d.original_amount + d.drawn - d.repaid
        d.paid_pct = percent(d.repaid, d.original_amount + d.drawn)
    return out


def refresh_debt_status(debt):
    out = outstanding_principal(debt)
    if out <= 0 and debt.status == Debt.Status.ACTIVE:
        debt.status = Debt.Status.PAID_OFF
        debt.save(update_fields=["status", "updated_at"])
    elif out > 0 and debt.status == Debt.Status.PAID_OFF:
        debt.status = Debt.Status.ACTIVE
        debt.save(update_fields=["status", "updated_at"])


# ------------------------------------------------------------ savings goals
def goal_current_amount(goal):
    return goal.contributions.aggregate(t=_sum(expression=F("amount")))["t"]


def goals_with_progress(user, today):
    cur = Subquery(SavingsContribution.objects.filter(goal=OuterRef("pk")).order_by().values("goal")
                   .annotate(s=Sum("amount")).values("s"), output_field=MONEY_FIELD)
    out = list(SavingsGoal.objects.filter(owner=user).annotate(current=Coalesce(cur, Value(ZERO))))
    for g in out:
        g.plan = contribution_plan(g.target_amount, g.current, g.target_date, today)
        g.pct = percent(g.current, g.target_amount)
    return out


def goal_progress_info(goal, today):
    current = goal_current_amount(goal)
    return {"current": current, "pct": percent(current, goal.target_amount),
            "plan": contribution_plan(goal.target_amount, current, goal.target_date, today)}


# --------------------------------------------------------- safe write paths
def _lock_accounts(*accounts):
    ids = sorted({a.pk for a in accounts if a is not None})
    if ids:
        list(Account.objects.select_for_update().filter(pk__in=ids).order_by("pk"))


def _check_principal(tx):
    out = outstanding_principal(tx.debt, exclude_tx_id=tx.pk)
    if tx.principal_amount > out:
        raise ValidationError(
            f"Principal {tx.principal_amount} is more than the outstanding principal {out} on {tx.debt.creditor}.")


def save_transaction(tx):
    """Validate + save under locks so concurrent writes cannot overspend a debt.

    Rows are locked in a fixed order (accounts by pk, then the debt) to avoid deadlocks.
    """
    if tx.pk and tx.is_linked_to_savings:
        raise ValidationError("This transfer belongs to a savings contribution. Edit the contribution instead.")
    with db_transaction.atomic():
        _lock_accounts(tx.account, tx.destination_account)
        old_debt_id = None
        if tx.pk:
            old_debt_id = Transaction.objects.filter(pk=tx.pk).values_list("debt_id", flat=True).first()
        debts = {d for d in (tx.debt, Debt.objects.filter(pk=old_debt_id).first() if old_debt_id else None) if d}
        for d in sorted(debts, key=lambda x: x.pk):
            Debt.objects.select_for_update().get(pk=d.pk)
        tx.full_clean()
        if tx.type == T.DEBT_PAYMENT:
            _check_principal(tx)
        tx.save()
        for d in debts:
            refresh_debt_status(Debt.objects.get(pk=d.pk))
    return tx


def delete_transaction(tx):
    if tx.is_linked_to_savings:
        raise ValidationError("This transfer belongs to a savings contribution. Delete the contribution instead.")
    with db_transaction.atomic():
        debt = Debt.objects.select_for_update().get(pk=tx.debt_id) if tx.debt_id else None
        tx.delete()
        if debt:
            refresh_debt_status(debt)


def save_drawdown(drawdown):
    with db_transaction.atomic():
        Debt.objects.select_for_update().get(pk=drawdown.debt_id)
        drawdown.full_clean()
        if drawdown.pk:   # lowering a drawdown must not push outstanding principal below zero
            if outstanding_principal(drawdown.debt, exclude_drawdown_id=drawdown.pk) + drawdown.amount < 0:
                raise ValidationError("That would make the repaid principal exceed the amount borrowed.")
        drawdown.save()
        refresh_debt_status(Debt.objects.get(pk=drawdown.debt_id))


def delete_drawdown(drawdown):
    with db_transaction.atomic():
        debt = Debt.objects.select_for_update().get(pk=drawdown.debt_id)
        if outstanding_principal(debt, exclude_drawdown_id=drawdown.pk) < 0:
            raise ValidationError("Deleting this drawdown would leave more principal repaid than borrowed. Delete or edit payments first.")
        drawdown.delete()
        refresh_debt_status(debt)


def save_debt(debt):
    """Original amount may not drop below what has already been repaid."""
    with db_transaction.atomic():
        if debt.pk:
            Debt.objects.select_for_update().get(pk=debt.pk)
            if outstanding_principal(debt) < 0:
                raise ValidationError("The original amount is lower than the principal already repaid.")
        debt.save()
        if debt.pk:
            refresh_debt_status(debt)


def save_contribution(contribution, from_account=None, to_account=None):
    """Create/update a contribution and, if both accounts are given, its linked transfer — atomically.

    With no accounts it is a pure earmark: no balance effect, never an expense.
    """
    if bool(from_account) != bool(to_account):
        raise ValidationError("Choose both accounts for a linked transfer, or neither to just record the contribution.")
    with db_transaction.atomic():
        contribution.full_clean(exclude=["transfer"])
        if from_account:
            _lock_accounts(from_account, to_account)
            for acc in (from_account, to_account):
                if acc.currency != contribution.goal.currency:
                    raise ValidationError("Account currency must match the savings goal currency.")
            tx = contribution.transfer or Transaction(owner=contribution.owner)
            tx.type, tx.date, tx.amount = T.TRANSFER, contribution.date, contribution.amount
            tx.account, tx.destination_account = from_account, to_account
            tx.description = f"Savings: {contribution.goal.name}"
            tx.full_clean()
            tx.save()
            contribution.transfer = tx
        elif contribution.transfer_id:      # switched to a plain earmark: remove the now-unneeded transfer
            old = contribution.transfer
            contribution.transfer = None
            contribution.save()
            old.delete()
        contribution.save()
    return contribution


def delete_contribution(contribution):
    with db_transaction.atomic():
        tx = contribution.transfer
        contribution.delete()
        if tx:
            tx.delete()


def delete_savings_goal(goal):
    with db_transaction.atomic():
        for c in goal.contributions.select_related("transfer"):
            delete_contribution(c)
        goal.delete()


# --------------------------------------------------------------- dashboard
def next_due_date(due_day, today):
    """Next date on/after `today` that falls on `due_day`; short months use their last day."""
    for offset in (0, 1):
        y, m = shift_month(today.year, today.month, offset)
        d = dt.date(y, m, min(due_day, calendar.monthrange(y, m)[1]))
        if d >= today:
            return d


def upcoming_bills(user, today, days=14):
    """Active debts with a due day and minimum payment falling due within `days`."""
    out = []
    for d in Debt.objects.filter(owner=user, status=Debt.Status.ACTIVE, due_day__isnull=False, minimum_payment__gt=0):
        due = next_due_date(d.due_day, today)
        if due and (due - today).days <= days:
            out.append({"debt": d, "due": due, "amount": d.minimum_payment, "days": (due - today).days})
    return sorted(out, key=lambda x: x["due"])


def dashboard_finance(user, today):
    cur = user.profile.currency
    has_accounts = Account.objects.filter(owner=user).exists()
    if not has_accounts:
        return {"has_accounts": False, "currency": cur}
    summary = month_summary(user, cur, today.year, today.month)
    budgets = [b for b in budgets_for_month(user, today.year, today.month) if b["budget"].currency == cur]
    budgets.sort(key=lambda b: (b["state"] == "ok", -b["pct"]))
    debts = [d for d in debts_with_balances(user) if d.currency == cur and d.status == Debt.Status.ACTIVE]
    goals = [g for g in goals_with_progress(user, today) if g.currency == cur and g.status == SavingsGoal.Status.ACTIVE]
    return {"has_accounts": True, "currency": cur, "summary": summary, "budgets": budgets[:4],
            "debt_outstanding": sum((d.outstanding for d in debts), ZERO), "debt_count": len(debts),
            "goals": goals[:3], "bills": upcoming_bills(user, today)}
