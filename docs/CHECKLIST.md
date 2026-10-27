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
## Phase 3 – finance  [todo]
## Phase 4 – future plans, interests  [todo]
## Phase 5 – reviews, insights, polish  [todo]
## Phase 6 – advanced  [todo]
