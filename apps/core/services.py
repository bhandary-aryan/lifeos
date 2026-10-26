"""Shared helpers for date/time handling in a user's own timezone."""
import zoneinfo

from django.utils import timezone


def user_zone(user):
    profile = getattr(user, "profile", None)
    name = getattr(profile, "timezone", None) or "UTC"
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return zoneinfo.ZoneInfo("UTC")


def local_now(user):
    return timezone.now().astimezone(user_zone(user))


def local_today(user):
    """The user's current calendar date (not the server's)."""
    return local_now(user).date()
