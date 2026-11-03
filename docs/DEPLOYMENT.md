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
| `LIFEOS_SITE_URL` | public base URL used for links in emails, e.g. `https://lifeos.example.com` |

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

## Scheduled jobs: email reminders
Email digests are opt-in per user (Settings → Preferences). Sending is done by one management command that you schedule:
```cron
*/30 * * * * cd /srv/lifeos && .venv/bin/python manage.py send_reminders >> /var/log/lifeos-reminders.log 2>&1
```
(or a systemd timer). Needs `EMAIL_*`, `DEFAULT_FROM_EMAIL` and `LIFEOS_SITE_URL` set. How it behaves:
- Each user's own timezone, preferred hour, quiet hours and frequency (daily / weekdays / weekly) are respected.
- At most one digest per user per local day; running it more often, or on two machines at once, cannot send duplicates (the day is claimed atomically before sending and released if delivery fails, so the next run retries).
- Nothing is sent when there is nothing to report; that day is still consumed.
- Emails contain only task/goal/payment titles the user already sees in the app; never journal or review text.
- Check without sending: `python manage.py send_reminders --dry-run`. The command prints a one-line summary (sent / nothing to say / not due / already handled / failed); alert on `failed > 0`.
- Test your SMTP settings first: `python manage.py sendtestemail you@example.com`.

## Pre-release checklist
- [ ] `python -m pytest` green; also run once with `LIFEOS_TEST_NOW` set to a month-end/Sunday.
- [ ] `check --deploy` reviewed; `collectstatic` succeeds.
- [ ] HTTPS working; cookies marked Secure; HSTS decision made.
- [ ] Backups and a restore test in place.
- [ ] If you enable email reminders: cron/timer installed, `send_reminders --dry-run` reviewed, SMTP tested with `sendtestemail`.
- [ ] Run `qa/visual_sweep.js` against a staging copy and look at a few screenshots; spot-check on a real phone and in Safari/Firefox (see qa/README.md).
