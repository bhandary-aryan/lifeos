# Architecture

Django monolith, server-rendered. One database (PostgreSQL), no background workers.

```
config/            settings (base/dev/prod), urls, wsgi/asgi
apps/core          OwnedModel, OwnerQuerysetMixin, timezone helpers, shared views/CSV helper, nav
apps/accounts      email User, Profile (preferences), onboarding, export, deletion, login/reset throttling, seed_demo
apps/goals         LifeArea, Goal, Milestone, progress services
apps/planner       Task, Habit, HabitLog; capacity maths (services.py); streak maths (habit_services.py)
apps/finance       Account, Category, Transaction, Debt(+Drawdown), Budget, SavingsGoal(+Contribution); calculations.py (pure), services.py
apps/future_plans  FuturePlan, PlanAction; affordability view
apps/interests     InterestItem (+ private image delivery)
apps/reflections   WeeklyReview, JournalEntry; insights.py (trends, weekly snapshot)
apps/dashboard     summary service, reminders.py (computed on demand)
```
Views stay thin; anything needed by more than one page lives in a `services.py`/`calculations.py` function so totals cannot disagree between pages.

## Ownership
Every private model inherits `OwnedModel` (`owner`, timestamps). Views either filter `owner=request.user` or use `OwnerQuerysetMixin`. Another user's object returns **404** (never 403), with a body identical to a genuinely missing object. Forms limit every foreign-key choice to the user's own rows, and models re-validate ownership in `clean()`.
Gotcha (tested): a subclass overriding `get_queryset()` replaces the mixin's filter.

## Time
Datetimes are stored in UTC and shown in the user's timezone (middleware). Calendar dates the user thinks in (due dates, habit logs, transaction dates) are plain `DateField`s. "Today" is always `core.services.local_today(user)`. Week boundaries use the user's week-start and are DST-safe (aware datetimes built per local midnight).

## Money rules (single source: `finance/services.py`)
- `Decimal(14,2)` everywhere; no floats.
- **Balance** = opening + income + transfers in − expenses − transfers out − debt payments (total). Derived on read; never stored.
- **Spending** = expenses + the *interest* part of debt payments. Transfers, principal repayments and savings contributions are never spending.
- **Net cash flow** = income − expenses − total debt payments.
- A transfer is one row. A debt payment is one row with `principal + interest = amount` (DB check). Principal may not exceed outstanding principal (checked under a row lock).
- Outstanding principal = borrowed + extra borrowing − principal repaid. Interest accrual is not modelled.
- Savings goal saved amount = sum of contributions; a contribution may be linked to a real transfer, edited and deleted together with it.
- Per-currency reports; no silent conversion; transfers between currencies are rejected.
- `RESTRICT` (not `PROTECT`) on accounts/debts/linked transfers: in-use records can't be deleted on their own, but deleting a whole user still cascades.
- Locking order: accounts by id, then debts. Concurrency tests use real parallel connections and were mutation-checked.

## Estimates
`finance/calculations.py` is pure and deterministic: months remaining (a partial month counts as one), monthly saving = remaining ÷ months rounded **up** to the cent, affordability, avalanche/snowball simulation. No returns, inflation or rate changes are assumed, and the UI labels results as estimates.

## Reminders and insights
Computed from live records on each request (nothing stored, nothing pushed). The top-bar bell fetches its count lazily so ordinary pages do not pay for it.
