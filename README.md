# LifeOS

Personal life management platform: goals, planner, habits, finances, future plans, hobbies and reviews.
Django monolith, PostgreSQL, server-rendered templates, Tailwind + DaisyUI, HTMX, Alpine.js, Chart.js, Lucide.
The product name is configurable via `LIFEOS_SITE_NAME`.

**Status:** Phases 1 (foundation) and 2 (goals, planner, habits) complete. See `docs/CHECKLIST.md`. Finance, future plans, interests and reviews show an honest "not built yet" page.

## Local setup
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
npm install && npm run build:css      # compiled CSS is generated; vendored JS is in static/vendor
cp .env.example .env                  # then edit values (never commit .env)
```

### PostgreSQL
```sql
CREATE ROLE lifeos WITH LOGIN PASSWORD 'change-me' CREATEDB;   -- CREATEDB lets pytest create its test DB
CREATE DATABASE lifeos OWNER lifeos;
```
Set `DATABASE_URL=postgres://lifeos:change-me@localhost:5432/lifeos`.

```bash
python manage.py migrate
python manage.py createsuperuser      # admin at /admin/
python manage.py runserver
npm run watch:css                     # while editing templates
```

## Tests
```bash
python -m pytest
```
Tests run against real PostgreSQL (a throwaway `test_lifeos` database).

## Architecture
- `config/` settings (`base`, `dev`, `prod`), URLs, WSGI/ASGI
- `apps/core/` shared base (`OwnedModel`, `OwnerQuerysetMixin`, timezone services, nav, placeholders)
- `apps/accounts/` user + profile, auth, onboarding, preferences, export, deletion
- `apps/goals/` life areas, goals, milestones, progress services (`goals/services.py`)
- `apps/planner/` tasks, habits, habit logs; capacity maths (`services.py`) and streak/consistency maths (`habit_services.py`)
- `apps/dashboard/` summary service + dashboard view (totals will be computed in `services.py`, not templates)
- Ownership: every private model inherits `OwnedModel`; views use `OwnerQuerysetMixin` so other users' objects return 404.
- Time: datetimes stored in UTC, displayed in the user's timezone (middleware); use `core.services.local_today(user)` for calendar dates.

## Production notes
Use `DJANGO_SETTINGS_MODULE=config.settings.prod` with: `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`,
`DJANGO_CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, SMTP settings. Run `python manage.py collectstatic --noinput`,
serve with `gunicorn config.wsgi` behind an HTTPS-terminating proxy that sets `X-Forwarded-Proto`.
Run `python manage.py check --deploy`. Remaining responsibilities not handled by this repo: TLS certificates,
database backups, secret management, log shipping, rate limiting on login/reset endpoints, and a real SMTP provider.
HSTS preload is opt-in (`DJANGO_HSTS_PRELOAD=true`) and is the one `check --deploy` warning left by default.

## Behaviour notes
- Goal progress: one mode per goal. Milestone/task modes are weighted; cancelled tasks are ignored; a goal with nothing to measure shows 0%.
- Habit streaks count scheduled days only; today never breaks a streak until it has passed. Pausing stops new days counting from the pause date and never deletes logs.
- Week/day capacity warnings compare estimated minutes of open tasks with your "available minutes per day" setting (default 480). They are suggestions only.

## Known limitations
- Finance, future plans, interests, reviews are not implemented yet (Phases 3-5).
- Recurring tasks, habit reminders, dashboard widget customisation and history charts are not implemented.
- Habit statistics for a *past* period that contained a pause count the paused days as missed (pause intervals are not stored).
- Habit weekly/monthly figures for "times per week" habits are an approximation for partial weeks/months.
- The quick-add Transaction entry is a disabled placeholder; no login throttling yet; password-reset email uses the console backend in dev.
- Email cannot be changed from Settings yet; no email verification at registration.
- Light/dark rendering and mobile layout were checked via HTML/tests only, not visually in a browser.
