"""Brute-force protection for sign-in and password-reset requests (no extra dependencies).

Design
- Failures are remembered as timestamps in Django's cache for WINDOW seconds.
- Two counters: per (email, client IP) -> a few guesses at one account; per client IP -> many accounts from one place.
  Keying the account counter by IP as well means a stranger cannot lock YOU out of your account from their own address.
- Counted identically for known and unknown emails, so responses never reveal whether an account exists.
- A successful sign-in clears that (email, IP) counter.
- Behind a reverse proxy REMOTE_ADDR is the proxy's address. Set LIFEOS_TRUST_X_FORWARDED_FOR=true ONLY when your proxy
  overwrites X-Forwarded-For; otherwise clients could spoof their address.
- The default cache is per-process memory. With several workers, set CACHE_URL to a shared cache so limits are global.
"""
import datetime as dt
import hashlib

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

WINDOW = 15 * 60
MAX_PER_ACCOUNT = 5
MAX_PER_IP = 20
MAX_RESET_REQUESTS = 5


def client_ip(request):
    if getattr(settings, "THROTTLE_TRUST_X_FORWARDED_FOR", False):
        fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _key(kind, *parts):
    return f"throttle:{kind}:" + hashlib.sha256("|".join(p.strip().lower() for p in parts).encode()).hexdigest()


def _recent(key):
    cutoff = timezone.now() - dt.timedelta(seconds=WINDOW)
    return [t for t in cache.get(key, []) if t > cutoff]


def _add(key):
    hits = _recent(key)
    hits.append(timezone.now())
    cache.set(key, hits, WINDOW)


def retry_after(key):
    hits = _recent(key)
    if not hits:
        return 0
    return max(1, int(WINDOW - (timezone.now() - min(hits)).total_seconds()))


# ---- sign-in
def login_blocked(request, email):
    ip = client_ip(request)
    return len(_recent(_key("acct", email, ip))) >= MAX_PER_ACCOUNT or len(_recent(_key("ip", ip))) >= MAX_PER_IP


def login_retry_after(request, email):
    ip = client_ip(request)
    return max(retry_after(_key("acct", email, ip)), retry_after(_key("ip", ip)))


def record_login_failure(request, email):
    ip = client_ip(request)
    _add(_key("acct", email, ip)); _add(_key("ip", ip))


def clear_login_failures(request, email):
    cache.delete(_key("acct", email, client_ip(request)))


# ---- password reset requests (every request counts, because each one can send an email)
def reset_blocked(request):
    return len(_recent(_key("reset", client_ip(request)))) >= MAX_RESET_REQUESTS


def record_reset_request(request):
    _add(_key("reset", client_ip(request)))
