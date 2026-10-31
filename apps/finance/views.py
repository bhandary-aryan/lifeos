import csv
import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import F, ProtectedError, Q, RestrictedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.models import CURRENCY_CHOICES
from apps.core.services import local_today, parse_date
from apps.core.csv_utils import csv_safe
from apps.core.views import confirm_and_delete, safe_next

from . import services as fin
from .calculations import month_bounds, shift_month, simulate_repayment
from .forms import (
    AccountForm, BudgetForm, CategoryForm, ContributionForm, DebtForm, DrawdownForm, PlannerForm,
    SavingsGoalForm, TransactionForm,
)
from .models import (
    Account, Budget, Category, Debt, DebtDrawdown, SavingsContribution, SavingsGoal, Transaction,
)

T = Transaction.Type
CURRENCIES = [c for c, _ in CURRENCY_CHOICES]


# ------------------------------------------------------------------ helpers
def _owned(model, request, pk):
    return get_object_or_404(model, pk=pk, owner=request.user)


def _add_validation_error(form, exc):
    try:
        form.add_error(None, exc)
    except ValueError:                      # error keyed on a field this form does not show
        form.add_error(None, "; ".join(exc.messages))


def _form_page(request, form, title, cancel_url, extra=None):
    return render(request, "components/form_page.html", {"form": form, "page_title": title, "cancel_url": cancel_url, "extra": extra})


_confirm = confirm_and_delete


def _month_param(request, today):
    ym = request.GET.get("month", "")
    try:
        y, m = (int(x) for x in ym.split("-"))
        dt.date(y, m, 1)
        return y, m
    except (ValueError, TypeError):
        return today.year, today.month


def _currency_param(request):
    c = request.GET.get("currency", "")
    return c if c in CURRENCIES else request.user.profile.currency


def _month_nav(y, m):
    py, pm = shift_month(y, m, -1)
    ny, nm = shift_month(y, m, 1)
    return f"{py}-{pm:02d}", f"{ny}-{nm:02d}"


# ----------------------------------------------------------------- overview
@login_required
def overview(request):
    user = request.user
    fin.ensure_default_categories(user)
    today = local_today(user)
    y, m = _month_param(request, today)
    cur = _currency_param(request)
    first, nxt = month_bounds(y, m)
    summary = fin.month_summary(user, cur, y, m)
    py, pm = shift_month(y, m, -1)
    budgets = [b for b in fin.budgets_for_month(user, y, m) if b["budget"].currency == cur]
    accounts = fin.accounts_with_balances(user)
    debts = [d for d in fin.debts_with_balances(user) if d.currency == cur and d.status == Debt.Status.ACTIVE]
    goals = [g for g in fin.goals_with_progress(user, today) if g.currency == cur and g.status == SavingsGoal.Status.ACTIVE]
    series = fin.monthly_series(user, cur, y, m, n=6)
    breakdown = fin.category_breakdown(user, cur, first, nxt)
    prev_m, next_m = _month_nav(y, m)
    ctx = {
        "page_title": "Finances", "tab": "overview", "year": y, "month": m, "month_label": first.strftime("%B %Y"), "currency": cur,
        "currencies": CURRENCIES, "summary": summary, "compare": fin.compare_periods(summary, fin.month_summary(user, cur, py, pm)),
        "budgets": budgets, "accounts": [a for a in accounts if a.is_active], "other_currency_accounts": sum(1 for a in accounts if a.is_active and a.currency != cur),
        "debts": debts, "debt_total": sum((d.outstanding for d in debts), fin.ZERO), "goals": goals,
        "chart": {"labels": [s["label"] for s in series], "income": [float(s["income"]) for s in series], "spending": [float(s["spending"]) for s in series],
                  "cat_labels": [r["name"] for r in breakdown], "cat_values": [float(r["total"]) for r in breakdown]},
        "breakdown": breakdown, "prev_month": prev_m, "next_month": next_m, "has_data": bool(accounts),
    }
    return render(request, "finance/overview.html", ctx)


@login_required
def reports(request):
    user = request.user
    today = local_today(user)
    y, m = _month_param(request, today)
    cur = _currency_param(request)
    year = int(request.GET["year"]) if request.GET.get("year", "").isdigit() and 2000 <= int(request.GET["year"]) <= 2200 else y
    first, nxt = month_bounds(y, m)
    this = fin.month_summary(user, cur, y, m)
    py, pm = shift_month(y, m, -1)
    ys = fin.year_summary(user, cur, year)
    prev_m, next_m = _month_nav(y, m)
    ctx = {
        "page_title": "Reports", "tab": "reports", "year": y, "month": m, "month_label": first.strftime("%B %Y"), "currency": cur,
        "currencies": CURRENCIES, "summary": this,
        "vs_prev_month": fin.compare_periods(this, fin.month_summary(user, cur, py, pm)),
        "vs_last_year": fin.compare_periods(this, fin.month_summary(user, cur, y - 1, m)),
        "breakdown": fin.category_breakdown(user, cur, first, nxt), "report_year": year, "ys": ys,
        "prev_month": prev_m, "next_month": next_m,
        "chart": {"labels": [s["label"] for s in ys["months"]], "income": [float(s["income"]) for s in ys["months"]],
                  "spending": [float(s["spending"]) for s in ys["months"]]},
    }
    return render(request, "finance/reports.html", ctx)


# ----------------------------------------------------------------- accounts
@login_required
def account_list(request):
    return render(request, "finance/account_list.html", {"page_title": "Accounts", "tab": "accounts", "accounts": fin.accounts_with_balances(request.user)})


@login_required
def account_form(request, pk=None):
    acc = _owned(Account, request, pk) if pk else None
    form = AccountForm(request.POST or None, instance=acc, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Account saved.")
        return redirect("finance:accounts")
    return _form_page(request, form, "Edit account" if acc else "New account", reverse("finance:accounts"))


@login_required
def account_delete(request, pk):
    acc = _owned(Account, request, pk)
    extra = "Accounts with transactions can't be deleted — mark them inactive instead (edit the account)."
    return _confirm(request, acc, "account", reverse("finance:accounts"), reverse("finance:accounts"), lambda a: a.delete(), "Account deleted.", extra)


# --------------------------------------------------------------- categories
@login_required
def categories(request):
    fin.ensure_default_categories(request.user)
    form = CategoryForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category added.")
        return redirect("finance:categories")
    rows = Category.objects.filter(owner=request.user)
    return render(request, "finance/categories.html", {"page_title": "Categories", "tab": "categories", "form": form,
                                                       "income": [c for c in rows if c.kind == "income"], "expense": [c for c in rows if c.kind == "expense"]})


@login_required
@require_POST
def category_delete(request, pk):
    cat = _owned(Category, request, pk)
    if cat.is_system:
        messages.error(request, "This category is managed by the app and can't be deleted.")
    else:
        cat.delete()
        messages.success(request, "Category deleted. Its transactions are now uncategorised and its budgets were removed.")
    return redirect("finance:categories")


# ------------------------------------------------------------- transactions
def _tx_queryset(user, g):
    qs = Transaction.objects.filter(owner=user).select_related("account", "destination_account", "category", "debt")
    if g.get("type") in Transaction.Type.values:
        qs = qs.filter(type=g["type"])
    if g.get("account", "").isdigit():
        qs = qs.filter(Q(account_id=int(g["account"])) | Q(destination_account_id=int(g["account"])))
    if g.get("category", "").isdigit():
        qs = qs.filter(category_id=int(g["category"]))
    if d := parse_date(g.get("date_from")):
        qs = qs.filter(date__gte=d)
    if d := parse_date(g.get("date_to")):
        qs = qs.filter(date__lte=d)
    if q := g.get("q", "").strip():
        qs = qs.filter(Q(description__icontains=q) | Q(category__name__icontains=q) | Q(debt__creditor__icontains=q))
    return qs.order_by(*{"date": ["date", "id"], "amount": ["-amount", "-date"], "amount_asc": ["amount", "-date"]}.get(g.get("sort"), ["-date", "-id"]))


@login_required
def transaction_list(request):
    user = request.user
    qs = _tx_queryset(user, request.GET)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    params = request.GET.copy(); params.pop("page", None)
    ctx = {"page_title": "Transactions", "tab": "transactions", "page_obj": page, "qs": params.urlencode(), "f": request.GET,
           "types": Transaction.Type.choices, "accounts": Account.objects.filter(owner=user), "categories": Category.objects.filter(owner=user)}
    return render(request, "finance/transaction_list.html", ctx)


@login_required
def transaction_form(request, pk=None):
    user = request.user
    fin.ensure_default_categories(user)
    tx = _owned(Transaction, request, pk) if pk else None
    if tx and tx.is_linked_to_savings:
        messages.info(request, "This transfer belongs to a savings contribution — edit the contribution instead.")
        return redirect("finance:savings_detail", pk=tx.savings_contribution.goal_id)
    initial = {"date": local_today(user)}
    if request.GET.get("type") in Transaction.Type.values:
        initial["type"] = request.GET["type"]
    if request.GET.get("debt", "").isdigit():
        initial.update(type=T.DEBT_PAYMENT, debt=int(request.GET["debt"]))
    form = TransactionForm(request.POST or None, instance=tx, user=user, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            fin.save_transaction(form.instance)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "Transaction saved.")
            return redirect(safe_next(request, reverse("finance:transactions")))
    return render(request, "finance/transaction_form.html", {"form": form, "page_title": "Edit transaction" if tx else "New transaction",
                                                             "cancel_url": safe_next(request, reverse("finance:transactions")),
                                                             "start_type": (form["type"].value() or T.EXPENSE)})


@login_required
def transaction_delete(request, pk):
    tx = _owned(Transaction, request, pk)
    if tx.is_linked_to_savings:
        messages.error(request, "This transfer belongs to a savings contribution. Delete the contribution instead.")
        return redirect("finance:transactions")
    back = reverse("finance:transactions")
    return _confirm(request, tx, "transaction", back, back, fin.delete_transaction, "Transaction deleted; balances were recalculated.")


_csv_safe = csv_safe


@login_required
def transactions_export(request):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="lifeos-transactions.csv"'
    w = csv.writer(resp)
    w.writerow(["date", "type", "amount", "currency", "account", "to_account", "category", "description", "debt", "principal", "interest"])
    for t in _tx_queryset(request.user, request.GET).iterator():
        w.writerow([t.date.isoformat(), t.type, t.amount, t.account.currency, _csv_safe(t.account.name),
                    _csv_safe(t.destination_account.name if t.destination_account else ""), _csv_safe(t.category.name if t.category else ""),
                    _csv_safe(t.description), _csv_safe(t.debt.creditor if t.debt else ""), t.principal_amount, t.interest_amount])
    return resp


# ------------------------------------------------------------------ budgets
@login_required
def budget_list(request):
    user = request.user
    fin.ensure_default_categories(user)
    today = local_today(user)
    y, m = _month_param(request, today)
    prev_m, next_m = _month_nav(y, m)
    return render(request, "finance/budgets.html", {"page_title": "Budgets", "tab": "budgets", "rows": fin.budgets_for_month(user, y, m), "year": y, "month": m,
                                                    "month_label": dt.date(y, m, 1).strftime("%B %Y"), "prev_month": prev_m, "next_month": next_m})


@login_required
def budget_form(request, pk=None):
    b = _owned(Budget, request, pk) if pk else None
    today = local_today(request.user)
    initial = {"year": today.year, "month": today.month}
    if (ym := request.GET.get("month")) and "-" in ym:
        try:
            initial["year"], initial["month"] = (int(x) for x in ym.split("-"))
        except ValueError:
            pass
    form = BudgetForm(request.POST or None, instance=b, user=request.user, initial=initial)
    back = reverse("finance:budgets") + (f"?month={b.year}-{b.month:02d}" if b else "")
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, "Budget saved.")
        return redirect(reverse("finance:budgets") + f"?month={saved.year}-{saved.month:02d}")
    return _form_page(request, form, "Edit budget" if b else "New budget", back)


@login_required
def budget_delete(request, pk):
    b = _owned(Budget, request, pk)
    back = reverse("finance:budgets") + f"?month={b.year}-{b.month:02d}"
    return _confirm(request, b, "budget", back, back, lambda o: o.delete(), "Budget deleted.")


# -------------------------------------------------------------------- debts
@login_required
def debt_list(request):
    debts = fin.debts_with_balances(request.user)
    totals = {}
    for d in debts:
        if d.status == Debt.Status.ACTIVE:
            totals[d.currency] = totals.get(d.currency, fin.ZERO) + d.outstanding
    return render(request, "finance/debt_list.html", {"page_title": "Debts", "tab": "debts", "debts": debts, "totals": totals})


@login_required
def debt_form(request, pk=None):
    debt = _owned(Debt, request, pk) if pk else None
    form = DebtForm(request.POST or None, instance=debt, user=request.user, initial={"start_date": local_today(request.user)})
    if request.method == "POST" and form.is_valid():
        try:
            fin.save_debt(form.instance)
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "Debt saved.")
            return redirect("finance:debt_detail", pk=form.instance.pk)
    return _form_page(request, form, "Edit debt" if debt else "New debt", reverse("finance:debts"))


@login_required
def debt_detail(request, pk):
    debt = _owned(Debt, request, pk)
    payments = list(debt.payments.select_related("account"))
    outstanding = fin.outstanding_principal(debt)
    ctx = {"page_title": debt.creditor, "tab": "debts", "debt": debt, "payments": payments, "drawdowns": debt.drawdowns.all(), "outstanding": outstanding,
           "repaid": sum((p.principal_amount for p in payments), fin.ZERO), "interest_paid": sum((p.interest_amount for p in payments), fin.ZERO),
           "drawdown_form": DrawdownForm(user=request.user, debt=debt, initial={"date": local_today(request.user)})}
    return render(request, "finance/debt_detail.html", ctx)


@login_required
def debt_delete(request, pk):
    debt = _owned(Debt, request, pk)
    extra = "A debt with payments can't be deleted — delete its payments first, or set its status to Closed."
    return _confirm(request, debt, "debt", reverse("finance:debt_detail", args=[debt.pk]), reverse("finance:debts"), lambda d: d.delete(), "Debt deleted.", extra)


@login_required
@require_POST
def drawdown_add(request, pk):
    debt = _owned(Debt, request, pk)
    form = DrawdownForm(request.POST, user=request.user, debt=debt)
    if form.is_valid():
        try:
            fin.save_drawdown(form.instance)
            messages.success(request, "Additional borrowing recorded.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    else:
        messages.error(request, "Not saved: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("finance:debt_detail", pk=debt.pk)


@login_required
def drawdown_delete(request, pk):
    dd = _owned(DebtDrawdown, request, pk)
    back = reverse("finance:debt_detail", args=[dd.debt_id])
    return _confirm(request, dd, "drawdown", back, back, fin.delete_drawdown, "Drawdown deleted.")


@login_required
def debt_planner(request):
    user = request.user
    form = PlannerForm(request.GET or None, initial={"currency": user.profile.currency})
    results = None
    if request.GET and form.is_valid():
        cur, budget = form.cleaned_data["currency"], form.cleaned_data["monthly_budget"]
        active = [d for d in fin.debts_with_balances(user) if d.currency == cur and d.status == Debt.Status.ACTIVE and d.outstanding > 0]
        spec = [{"id": d.pk, "name": d.creditor, "balance": d.outstanding, "rate": d.interest_rate, "minimum": d.minimum_payment} for d in active]
        results = {"currency": cur, "budget": budget, "count": len(spec),
                   "avalanche": simulate_repayment(spec, budget, "avalanche"), "snowball": simulate_repayment(spec, budget, "snowball")}
    return render(request, "finance/debt_planner.html", {"page_title": "Repayment planner", "tab": "debts", "form": form, "results": results})


# ------------------------------------------------------------------ savings
@login_required
def savings_list(request):
    return render(request, "finance/savings_list.html", {"page_title": "Savings goals", "tab": "savings", "goals": fin.goals_with_progress(request.user, local_today(request.user))})


@login_required
def savings_form(request, pk=None):
    goal = _owned(SavingsGoal, request, pk) if pk else None
    form = SavingsGoalForm(request.POST or None, instance=goal, user=request.user)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, "Savings goal saved.")
        return redirect("finance:savings_detail", pk=saved.pk)
    return _form_page(request, form, "Edit savings goal" if goal else "New savings goal", reverse("finance:savings"))


@login_required
def savings_detail(request, pk):
    goal = _owned(SavingsGoal, request, pk)
    today = local_today(request.user)
    ctx = {"page_title": goal.name, "tab": "savings", "goal": goal, "info": fin.goal_progress_info(goal, today),
           "contributions": goal.contributions.select_related("transfer__account", "transfer__destination_account")}
    return render(request, "finance/savings_detail.html", ctx)


@login_required
def savings_delete(request, pk):
    goal = _owned(SavingsGoal, request, pk)
    linked = goal.contributions.filter(transfer__isnull=False).count()
    plan_note = " The future plan linked to it is kept, just unlinked." if hasattr(goal, "future_plan") else ""
    extra = (f"{linked} linked account transfer{'s' if linked != 1 else ''} will also be removed, which changes the balances of the accounts involved." if linked
             else "Its contribution records will be removed. No account balances change.") + plan_note
    return _confirm(request, goal, "savings goal", reverse("finance:savings_detail", args=[goal.pk]), reverse("finance:savings"), fin.delete_savings_goal, "Savings goal deleted.", extra)


@login_required
def contribution_form(request, pk=None, goal_pk=None):
    contribution = _owned(SavingsContribution, request, pk) if pk else None
    goal = contribution.goal if contribution else _owned(SavingsGoal, request, goal_pk)
    form = ContributionForm(request.POST or None, instance=contribution, user=request.user, goal=goal, initial={"date": local_today(request.user)})
    if request.method == "POST" and form.is_valid():
        try:
            fin.save_contribution(form.instance, form.cleaned_data.get("from_account"), form.cleaned_data.get("to_account"))
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "Contribution saved.")
            return redirect("finance:savings_detail", pk=goal.pk)
    return _form_page(request, form, "Edit contribution" if contribution else "Add contribution", reverse("finance:savings_detail", args=[goal.pk]),
                      extra="Optionally choose two accounts to record the real money movement as a transfer. A transfer is never counted as spending.")


@login_required
def contribution_delete(request, pk):
    c = _owned(SavingsContribution, request, pk)
    back = reverse("finance:savings_detail", args=[c.goal_id])
    extra = "The linked account transfer will also be removed." if c.transfer_id else ""
    return _confirm(request, c, "contribution", back, back, fin.delete_contribution, "Contribution deleted.", extra)
