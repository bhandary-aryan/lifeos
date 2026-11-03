# Security notes

Django's defaults are a good start, not a guarantee. This lists what the app does and what remains your responsibility.

## Implemented (and covered by tests)
- Authentication required for every private page; anonymous requests redirect to sign-in.
- Strict per-user isolation (404 for other users' objects, identical to missing), plus form-level and model-level ownership validation.
- CSRF protection on all state-changing requests; a friendly 403 page that does not reveal the technical reason.
- Passwords hashed by Django (min length 10, common/numeric/similarity validators). Reset links expire after 3 hours; the reset response never reveals whether an email has an account.
- **Throttling:** 5 failed sign-ins per (email, address) and 20 per address within 15 minutes; 5 password-reset requests per address per 15 minutes. Counted identically for unknown emails. A stranger cannot lock you out from their own address.
- Output escaping by Django templates (no `|safe` on user input). ORM only.
- CSV exports neutralise spreadsheet formula injection.
- Uploaded images: format whitelist (JPEG/PNG/GIF/WebP), size/pixel limits, random file names, delivered only through an owner-checked view with `nosniff` and a restrictive CSP; removed on replace/delete/account deletion.
- Secrets only from the environment; `.env` is git-ignored. No bank credentials are stored or requested.
- Error pages show no stack traces, paths or exception text.
- Secure cookies, HSTS, SSL redirect, nosniff, referrer policy and `X-Frame-Options: DENY` in `config.settings.prod`. `manage.py check --deploy` is clean except the opt-in HSTS *preload* notice.

- Email digests are opt-in, contain only titles the user already sees, never journal/review text, cannot carry user-supplied header content (subject is built only from the site name and counts), and are sent at most once per user per local day.

## Not provided — your job in production
- TLS certificates and termination; a proxy that sets `X-Forwarded-Proto` (and overwrites `X-Forwarded-For` if you enable `LIFEOS_TRUST_X_FORWARDED_FOR`).
- A shared cache (`CACHE_URL`) so throttling is global across workers.
- Database and media backups, disk/database encryption at rest, secret management, log shipping, patching, monitoring.
- Application-level encryption of journal/review text (they are stored as plain text).
- Malware scanning of uploads; email verification at registration; two-factor authentication.
- A rate limiter / WAF in front of the app for volumetric abuse.
- An email provider for password-reset mail and reminder digests (dev prints to the console). Email itself is not end-to-end encrypted; digests list task titles, so tell users that.

## Reporting issues
Treat any report of cross-user data exposure as critical. `tests/test_*ownership*.py` walk every detail/edit/delete/action URL as another user; extend them when adding endpoints.
