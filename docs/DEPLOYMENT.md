# Deployment

## Required environment
| Variable | Purpose |
|---|---|
| `DJANGO_SETTINGS_MODULE=config.settings.prod` | production settings (DEBUG off, secure cookies, HSTS, SSL redirect) |
| `DJANGO_SECRET_KEY` | long random value; rotating it logs everyone out |
| `DJANGO_ALLOWED_HOSTS` | comma-separated host names (required) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | e.g. `https://lifeos.example.com` |
| `DATABASE_URL` | `postgres://user:pass@host:5432/dbname` |
| `LIFEOS_MEDIA_ROOT` | persistent directory for uploaded images (not web-exposed) |
| `CACHE_URL` | shared cache for login throttling (Redis/Memcached) when running several workers |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | SMTP for password-reset mail |
| `LIFEOS_TRUST_X_FORWARDED_FOR` | only if your proxy overwrites that header |

## Steps
```bash
pip install -r requirements.txt
npm ci && npm run build:css            # or build CSS in CI and ship static/css/app.css
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy        # expect only the HSTS-preload notice unless you opt in
gunicorn config.wsgi --workers 3 --bind 127.0.0.1:8000
```
Serve over HTTPS behind a proxy that sets `X-Forwarded-Proto: https`. Static files are served by WhiteNoise (hashed, compressed). **Do not** expose `LIFEOS_MEDIA_ROOT` through the web server — images must go through the app's owner check.

A Redis cache needs the `redis` package (`pip install redis`) and e.g. `CACHE_URL=rediscache://host:6379/1`.

## Backups and restore
Back up the PostgreSQL database **and** the media directory together; test a restore. Account deletion removes the user's rows and image files, but not copies in your backups.

## Scheduled jobs
None are required today. If you later add email reminders, use a real scheduler (cron/systemd timer running a management command, or Celery beat) — never a request-triggered hack — and document it here.

## Pre-release checklist
- [ ] `python -m pytest` green; also run once with `LIFEOS_TEST_NOW` set to a month-end/Sunday.
- [ ] `check --deploy` reviewed; `collectstatic` succeeds.
- [ ] HTTPS working; cookies marked Secure; HSTS decision made.
- [ ] Backups and a restore test in place.
- [ ] You have looked at the UI in real browsers (light/dark, phone width) — see Known limitations in the README.
