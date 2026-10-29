# LifeOS implementation checklist

## Phase 0 – architecture  [done]
## Phase 1 – foundation  [done]
- [x] Django 5.2 project, split settings (base/dev/prod), env-driven secrets, `.env.example`
- [x] PostgreSQL 16 configured via `DATABASE_URL`; migrations applied
- [x] Custom email-based `accounts.User` (case-insensitive unique email) + `Profile`
- [x] Register / login / logout (POST) / password reset (Django flow, console email in dev)
- [x] Optional 3-step onboarding (skippable), settings page, theme preference (light/dark/device)
- [x] Private data export (JSON) and account deletion (password + confirmation)
- [x] Base layout: sidebar (desktop), drawer + bottom nav (mobile), top bar, profile menu, messages
- [x] Dashboard shell with honest empty states (no fabricated figures)
- [x] `core`: OwnedModel, OwnerQuerysetMixin, timezone helpers
- [x] 62 pytest tests passing; `check --deploy` run
## Phase 2 – goals, planner, habits  [done, with listed gaps]
- [x] Per-user life areas (9 defaults seeded, custom add/remove, case-insensitive unique)
- [x] Goals (all spec fields), milestones (weight/order/due/status), list + card layouts, filters, sort, search, pagination, detail page
- [x] Progress modes manual / milestone / task via annotated subqueries (no row multiplication, no division by zero, task counted once)
- [x] Tasks: CRUD, statuses, priority, estimates, time block, goal/milestone links, Today/Upcoming/Overdue/All, search/filter/sort/pagination, quick-add
- [x] Day view and week plan with available-minutes capacity warnings (suggestion only, never auto-reschedules)
- [x] Habits: daily / selected weekdays / times-per-week, logs (DB-unique per day), streaks, best streak, weekly + monthly consistency, pause/resume, weekly tracker, month calendar
- [x] Dashboard: today's priorities, overdue, due soon, active goals, habits today, complete/log in place
- [x] Export includes Phase 2 data; account deletion removes it
- [x] 191 pytest tests passing (incl. cross-user checks on every Phase 2 URL)
- [ ] Recurring tasks (explicit recurrence rules) – NOT implemented; deferred
- [ ] Habit reminder preference, task habit/life-area association – NOT implemented
- [ ] Dashboard widget hide/rearrange – NOT implemented
- [ ] Charts for goal-completion and habit history – deferred to Phase 5 insights
## Phase 3 – finance  [done, with listed gaps]
- [x] Decimal(14,2) money everywhere; balances derived (never stored); DB CHECK constraints per transaction type
- [x] Accounts (6 types, per-account currency, opening balance, inactive), categories (defaults + custom + managed "Debt interest")
- [x] Transactions: income, expense, transfer (one row, never income/spending), debt payment (principal + interest, must reconcile)
- [x] Filters, search, sort, date range, pagination, CSV export (formula-injection safe)
- [x] Budgets per category/month/currency with approaching (>=80%) and exceeded states
- [x] Reports: month summary, category breakdown, 6-month trend, year table, comparison vs previous month and same month last year
- [x] Debts: principal tracker, additional borrowing, payment history, auto paid-off/reopen, overpayment blocked, due-day bills on dashboard
- [x] Repayment planner (avalanche vs snowball), labelled as estimates
- [x] Savings goals: current amount derived from contributions; optional linked transfer; monthly estimate; achieved/overdue/no-date states
- [x] Locking: accounts by pk then debts; real parallel-connection tests (mutation-checked: the debt-lock test fails without the lock)
- [x] FK relations use RESTRICT so in-use accounts/debts can't be deleted but account deletion still cascades
- [x] Dashboard finance section; data export and account deletion cover finance
- [ ] CSV IMPORT with review step – NOT implemented (planned; export only)
- [ ] Link from a savings goal to a future plan – added in Phase 4 (future_plans app does not exist yet)
- [ ] Interest accrual / amortisation schedules – NOT modelled (principal tracker only)
- [ ] Multi-currency conversion – deliberately not done; reports are per currency
- [ ] Recurring transactions, bank connections – out of scope (Phase 6)
## Phase 4 – future plans, interests  [done, with listed gaps]
- [x] Future plans: category, priority, status, target date, estimated cost, linked goal, linked savings goal (one goal per plan), action items
- [x] Estimated cost / savings-goal target / actual saved kept as three separate figures; monthly estimate = remaining / months left; what-if date recalculates without saving
- [x] Achieved, overdue ("needs updating") and no-date states handled; no division by zero; past dates never shown as achievable
- [x] One-click "create & link savings goal" from a plan; savings goal page links back to its plan
- [x] Affordability check (user-entered assumptions; pre-fill from last 3 full months' net cash flow; wording never promises affordability)
- [x] Hobbies / favourites / bucket list: 11 kinds, enjoy-vs-want-to-try statuses, tags, priority, notes, optional image
- [x] Bucket-list extras (target date, cost, linked plan, optional "add to planner") only on bucket-list items
- [x] Images: random file names, format whitelist (JPEG/PNG/GIF/WebP), size limit, served only through an owner-checked view, files removed on replace/clear/delete/account deletion
- [x] Dashboard: plans within 90 days; a gentle "something to look forward to" card (no streaks, hidden when empty)
- [x] Data export and account deletion cover plans and interests
- [ ] Scenario settings (returns/inflation) for affordability – NOT implemented; deliberately assumption-free
- [ ] Image thumbnails / resizing – NOT implemented (originals are served)
- [ ] Tying plans to a calendar view – NOT implemented
## Phase 5 – reviews, insights, polish  [todo]
## Phase 6 – advanced  [todo]
