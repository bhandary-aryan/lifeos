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
## Phase 2 – goals, planner, habits  [todo]
## Phase 3 – finance  [todo]
## Phase 4 – future plans, interests  [todo]
## Phase 5 – reviews, insights, polish  [todo]
## Phase 6 – advanced  [todo]
