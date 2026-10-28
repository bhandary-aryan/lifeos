"""Real parallel connections: locking must stop simultaneous writes breaking financial invariants."""
import threading
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.core.services import local_today
from apps.finance import services as fin
from apps.finance.models import Account, Debt, Transaction, SavingsContribution, SavingsGoal

T = Transaction.Type


def run_parallel(fn, n):
    """Run fn(i) in n threads at the same moment; returns list of exceptions-or-results."""
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        try:
            barrier.wait(timeout=10)
            results[i] = fn(i)
        except Exception as exc:       # noqa: BLE001 - we want to inspect every outcome
            results[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    return results


@pytest.mark.django_db(transaction=True)
def test_simultaneous_payments_from_different_accounts_cannot_overpay_a_debt(user):
    """Different paying accounts mean the account locks do NOT serialise these; only the debt lock can."""
    accts = [Account.objects.create(owner=user, name=f"Acc{i}", opening_balance=D("10000"), currency="GBP") for i in range(4)]
    debt = Debt.objects.create(owner=user, creditor="Bank", original_amount=D("100"), currency="GBP", start_date=local_today(user))

    def attempt(i):
        tx = Transaction(owner=user, type=T.DEBT_PAYMENT, date=local_today(user), amount=D("80"), account=accts[i],
                         debt=debt, principal_amount=D("80"), interest_amount=D("0"))
        return fin.save_transaction(tx)

    results = run_parallel(attempt, 4)
    ok = [r for r in results if isinstance(r, Transaction)]
    rejected = [r for r in results if isinstance(r, ValidationError)]
    unexpected = [r for r in results if not isinstance(r, (Transaction, ValidationError))]
    assert not unexpected, unexpected
    assert len(ok) == 1 and len(rejected) == 3                               # only one 80 fits into 100
    assert fin.outstanding_principal(Debt.objects.get(pk=debt.pk)) == D("20.00")
    total_out = sum(10000 - fin.account_balances(user)[a.pk] for a in accts)
    assert total_out == D("80.00")                                            # cash left exactly once


@pytest.mark.django_db(transaction=True)
def test_opposing_transfers_do_not_deadlock_and_conserve_money(user):
    a = Account.objects.create(owner=user, name="A", opening_balance=D("500"), currency="GBP")
    b = Account.objects.create(owner=user, name="B", opening_balance=D("500"), currency="GBP")

    def attempt(i):
        src, dst = (a, b) if i % 2 == 0 else (b, a)          # opposite directions -> classic deadlock shape
        tx = Transaction(owner=user, type=T.TRANSFER, date=local_today(user), amount=D("10"), account=src, destination_account=dst)
        return fin.save_transaction(tx)

    results = run_parallel(attempt, 8)
    assert all(isinstance(r, Transaction) for r in results), results
    bal = fin.account_balances(user)
    assert bal[a.pk] + bal[b.pk] == D("1000.00")                              # nothing created or lost
    assert Transaction.objects.filter(owner=user).count() == 8


@pytest.mark.django_db(transaction=True)
def test_parallel_income_inserts_all_count_exactly_once(user):
    a = Account.objects.create(owner=user, name="A", opening_balance=D("0"), currency="GBP")

    def attempt(i):
        return fin.save_transaction(Transaction(owner=user, type=T.INCOME, date=local_today(user), amount=D("12.50"), account=a))

    results = run_parallel(attempt, 6)
    assert all(isinstance(r, Transaction) for r in results), results
    assert fin.account_balances(user)[a.pk] == D("75.00")


@pytest.mark.django_db(transaction=True)
def test_parallel_linked_contributions_keep_goal_and_balances_in_step(user):
    cur = Account.objects.create(owner=user, name="Cur", opening_balance=D("1000"), currency="GBP")
    sav = Account.objects.create(owner=user, name="Sav", opening_balance=D("0"), currency="GBP")
    goal = SavingsGoal.objects.create(owner=user, name="G", target_amount=D("500"), currency="GBP")

    def attempt(i):
        c = SavingsContribution(owner=user, goal=goal, date=local_today(user), amount=D("20"))
        return fin.save_contribution(c, cur, sav)

    results = run_parallel(attempt, 5)
    assert all(isinstance(r, SavingsContribution) for r in results), results
    bal = fin.account_balances(user)
    assert fin.goal_current_amount(goal) == D("100.00")
    assert bal[cur.pk] == D("900.00") and bal[sav.pk] == D("100.00")          # goal total == money actually moved
    assert Transaction.objects.filter(owner=user, type=T.TRANSFER).count() == 5
