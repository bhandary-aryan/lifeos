"""Email reminder digests (opt-in), designed to be run on a schedule, e.g. every 30 minutes.

Rules, per user, evaluated in the USER's own timezone:
  - needs both "show reminders" and "email me a digest" switched on, and an active account;
  - at most ONE digest decision per local calendar day (stored in Profile.last_digest_on, claimed atomically);
  - not before the user's chosen hour, and never inside their quiet hours (it simply goes out at the first allowed run);
  - frequency: daily, weekdays only, or once a week on the first day of the user's week;
  - nothing is sent when there is nothing to say; the day is still consumed, so a quiet morning never turns into
    a surprise mid-afternoon email.
Running the command twice, or two copies at once, cannot produce duplicate mail: the day is claimed with a conditional
UPDATE before anything is sent, and released again if sending fails so the next run can retry.
Only titles the user already sees in the app are included. Journal and review text are never emailed.
"""
import datetime as dt
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Profile
from apps.core.services import user_zone
from apps.planner.services import week_start_for

from .reminders import INFO, SOON, URGENT, build_reminders

log = logging.getLogger(__name__)


@dataclass
class Result:
    sent: int = 0
    skipped_not_due: int = 0
    skipped_empty: int = 0
    skipped_claimed: int = 0
    failed: int = 0
    considered: int = 0

    def summary(self):
        return (f"considered {self.considered}: sent {self.sent}, nothing to say {self.skipped_empty}, not due {self.skipped_not_due}, "
                f"already handled {self.skipped_claimed}, failed {self.failed}")


def in_quiet_hours(hour, start, end):
    """True if `hour` falls in [start, end); the window may wrap midnight (e.g. 22 -> 7)."""
    if start is None or end is None or start == end:
        return False
    return start <= hour < end if start < end else (hour >= start or hour < end)


def is_due(profile, local_now):
    """Pure scheduling decision for one user at one local moment (no database access)."""
    if not (profile.reminders_enabled and profile.email_reminders):
        return False
    today = local_now.date()
    if profile.last_digest_on and profile.last_digest_on >= today:
        return False
    if profile.email_frequency == Profile.EmailFrequency.WEEKDAYS and today.weekday() >= 5:
        return False
    if profile.email_frequency == Profile.EmailFrequency.WEEKLY and today != week_start_for(today, profile.week_start):
        return False
    if local_now.hour < profile.email_digest_hour:
        return False
    if in_quiet_hours(local_now.hour, profile.quiet_start_hour, profile.quiet_end_hour):
        return False
    return True


def render_digest(user, items, today):
    groups = {URGENT: "Needs attention now", SOON: "Coming up", INFO: "For your information"}
    lines = [f"Hello {user.get_short_name()},", "", f"Here is what needs attention on {today:%A %-d %B}:", ""]
    for level in (URGENT, SOON, INFO):
        chunk = [i for i in items if i["level"] == level]
        if chunk:
            lines.append(groups[level])
            for i in chunk:
                lines.append(f"  - {i['title']} — {i['detail']}")
                lines.append(f"    {settings.SITE_URL}{i['url']}")
            lines.append("")
    lines += ["You are receiving this because you turned on email reminders. Change the time, frequency or quiet hours, or turn them off:",
              f"{settings.SITE_URL}/accounts/settings/"]
    n = len(items)
    urgent = sum(1 for i in items if i["level"] == URGENT)
    subject = f"[{settings.SITE_NAME}] {n} thing{'s' if n != 1 else ''} to look at" + (f" ({urgent} urgent)" if urgent else "")
    return subject, "\n".join(lines)


def send_due_digests(now=None, dry_run=False):
    now = now or timezone.now()
    res = Result()
    profiles = (Profile.objects.filter(user__is_active=True, reminders_enabled=True, email_reminders=True).select_related("user"))
    for p in profiles.iterator():
        res.considered += 1
        local_now = now.astimezone(user_zone(p.user))
        if not is_due(p, local_now):
            res.skipped_not_due += 1
            continue
        today = local_now.date()
        previous = p.last_digest_on
        if not dry_run:
            claimed = (Profile.objects.filter(pk=p.pk).filter(Q(last_digest_on__isnull=True) | Q(last_digest_on__lt=today)).update(last_digest_on=today))
            if not claimed:                                   # another run got there first
                res.skipped_claimed += 1
                continue
        items = build_reminders(p.user, today)
        if not items:
            res.skipped_empty += 1
            continue
        if dry_run:
            res.sent += 1
            continue
        subject, body = render_digest(p.user, items, today)
        try:
            EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [p.user.email]).send(fail_silently=False)
            res.sent += 1
        except Exception:                                      # noqa: BLE001 - any delivery failure must release the claim
            log.exception("digest delivery failed for user id %s", p.user_id)
            Profile.objects.filter(pk=p.pk, last_digest_on=today).update(last_digest_on=previous)
            res.failed += 1
    return res
