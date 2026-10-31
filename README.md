# LifeOS

A personal life-management platform: **goals, planner, habits, finances, future plans, hobbies & bucket list, weekly reviews, journal and insights** in one private, server-rendered Django app. The product name is configurable (`LIFEOS_SITE_NAME`).

Stack: Python 3.12 · Django 5.2 LTS · PostgreSQL 16 · Django templates · Tailwind 3 + DaisyUI 4 · HTMX · Alpine.js · Chart.js · Lucide. No SPA, no microservices, no external APIs.

## What it does

| Area | Highlights |
|---|---|
| Accounts | Email login, optional 3-step onboarding, per-user currency/timezone/week start/theme, password reset, data export (JSON + CSV), account deletion |
| Goals | Life areas, goals, weighted milestones, progress by manual %, milestones or tasks (a task is never counted twice) |
| Planner | Tasks, Today/Week/Upcoming/Overdue/All, time-available warnings (suggestions only), quick add |
| Habits | Daily / chosen weekdays / N per week, streaks, weekly and monthly consistency, pause without losing history |
| Finance | Accounts, income/expense/transfer/debt payment, budgets, reports, debts (principal vs interest), repayment planner, savings goals |
| Future plans | Cost vs savings target vs actually-saved kept separate, monthly estimate, what-if date, affordability check |
| Interests | Hobbies, favourites, bucket list, tags, private images, optional "add to planner" |
| Reflection | Weekly review with a data snapshot, private journal, 12-month insights with accessible data tables |
| Reminders | In-app, computed on demand, respecting your on/off and look-ahead settings |

Nothing on any page is made up: every figure is calculated from your records, and empty states say so.

## Local setup
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
npm install && npm run vendor && npm run build:css   # vendor copies JS libs into static/vendor (and strips source-map comments); build:css compiles Tailwind
cp .env.example .env                      # then edit (never commit .env)
```
PostgreSQL (the only supported database):
```sql
CREATE ROLE lifeos WITH LOGIN PASSWORD 'change-me' CREATEDB;   -- CREATEDB lets pytest create its test database
CREATE DATABASE lifeos OWNER lifeos;
```
Set `DATABASE_URL=postgres://lifeos:change-me@localhost:5432/lifeos`, then:
```bash
python manage.py migrate
python manage.py createsuperuser          # admin at /admin/ (maintenance only)
python manage.py runserver
npm run watch:css                         # while editing templates
```
### Optional: demo data (development only)
```bash
python manage.py seed_demo                # prints a demo login
```
Refuses to run unless `DEBUG` is on. It creates one account `demo@demo.lifeos.invalid`; every item is labelled **[Demo]** and a banner shows while signed in as it. Re-running rebuilds only that account.

## Tests
```bash
python -m pytest                          # needs the PostgreSQL role above
LIFEOS_TEST_NOW=2028-02-29T23:30 python -m pytest   # run the whole suite "as if" it were another moment
```
The suite covers calculations, integrity rules, ownership (every detail/edit/delete URL as another user), CSRF, concurrency with real parallel connections, accessibility (computed WCAG contrast + an HTML lint of ~40 rendered pages), timezone/DST boundaries and query-count regressions. `LIFEOS_TEST_NOW` exists to catch weekday/month-end/year-end assumptions.

## Documentation
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) – apps, models, money rules, calculations
- [docs/SECURITY.md](docs/SECURITY.md) – what is protected, how, and what is *not*
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) – production settings, environment, checklist
- [docs/CHECKLIST.md](docs/CHECKLIST.md) – implementation status per phase, with gaps

## Known limitations
- **Not implemented:** email/push reminders and quiet hours (need a scheduler), recurring tasks and transactions, CSV *import*, bank connections, currency conversion, interest-accrual/amortisation schedules, dashboard widget rearranging, calendar-app integration, offline/PWA support, AI-assisted features.
- Journal/review text and uploaded images are stored **unencrypted** at the application level (protect them with disk/database encryption and backups).
- Uploaded images are served as uploaded: no resizing, no malware scanning.
- Habit statistics for a past period that contained a pause count the paused days as missed; "N per week" figures are approximate for partial weeks/months.
- Reports are per currency; amounts in different currencies are never added or converted.
- Visual layouts and the Chart.js charts were verified through rendered HTML, data tests and an accessibility lint, **not** by looking at them in a real browser or on real devices. Please do that before relying on the design.
- The login throttle uses the cache; with several workers configure a shared cache (see docs/DEPLOYMENT.md).
