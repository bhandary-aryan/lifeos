import datetime as dt
from io import StringIO

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from apps.dashboard.digest import in_quiet_hours, is_due, render_digest, send_due_digests

pytestmark = pytest.mark.django_db
UTC = dt.timezone.utc
WED_9 = dt.datetime(2026, 3, 18, 9, 0, tzinfo=UTC)        # Wednesday 09:00 UTC (= 09:00 in London in March)


def opt_in(user, **kw):
    p = user.profile
    p.email_reminders = True
    p.timezone = kw.pop("timezone", "Europe/London")
    for k, v in kw.items():
        setattr(p, k, v)
    p.save()
    return p


def local(profile, y, m, d, h):
    """A local datetime in the user's own zone."""
    from zoneinfo import ZoneInfo
    return dt.datetime(y, m, d, h, 0, tzinfo=ZoneInfo(profile.timezone))


# --------------------------------------------------------------- quiet hours
@pytest.mark.parametrize("hour,start,end,expected", [
    (23, 22, 7, True), (3, 22, 7, True), (7, 22, 7, False), (12, 22, 7, False), (22, 22, 7, True),     # wraps midnight; end exclusive
    (13, 12, 14, True), (14, 12, 14, False), (11, 12, 14, False),                                     # same-day window
    (5, None, None, False), (5, 8, None, False), (5, None, 8, False), (5, 6, 6, False),               # not configured / empty window
])
def test_in_quiet_hours(hour, start, end, expected):
    assert in_quiet_hours(hour, start, end) is expected


# -------------------------------------------------------------- pure rules
def test_not_due_unless_both_switches_are_on(user):
    p = opt_in(user)
    assert is_due(p, local(p, 2026, 3, 18, 9))
    p.email_reminders = False
    assert not is_due(p, local(p, 2026, 3, 18, 9))
    p.email_reminders, p.reminders_enabled = True, False
    assert not is_due(p, local(p, 2026, 3, 18, 9))


def test_default_for_new_users_is_off(user):
    assert user.profile.email_reminders is False and user.profile.email_digest_hour == 8 and user.profile.quiet_start_hour is None


def test_digest_hour_gate(user):
    p = opt_in(user, email_digest_hour=8)
    assert not is_due(p, local(p, 2026, 3, 18, 7)) and is_due(p, local(p, 2026, 3, 18, 8)) and is_due(p, local(p, 2026, 3, 18, 22))


def test_quiet_hours_delay_the_digest_to_the_first_allowed_hour(user):
    p = opt_in(user, email_digest_hour=7, quiet_start_hour=22, quiet_end_hour=9)
    assert [is_due(p, local(p, 2026, 3, 18, h)) for h in (7, 8, 9, 10)] == [False, False, True, True]
    assert not is_due(p, local(p, 2026, 3, 18, 23))                         # and quiet again late at night


def test_once_per_local_day(user):
    p = opt_in(user)
    p.last_digest_on = dt.date(2026, 3, 18)
    assert not is_due(p, local(p, 2026, 3, 18, 15)) and is_due(p, local(p, 2026, 3, 19, 9))
    p.last_digest_on = dt.date(2026, 3, 20)                                  # a future marker (clock moved back) also blocks
    assert not is_due(p, local(p, 2026, 3, 19, 9))


@pytest.mark.parametrize("day,expected", [(16, True), (20, True), (21, False), (22, False)])        # Mon, Fri, Sat, Sun
def test_weekdays_frequency(user, day, expected):
    p = opt_in(user, email_frequency="weekdays")
    assert is_due(p, local(p, 2026, 3, day, 9)) is expected


@pytest.mark.parametrize("week_start,day,expected", [(0, 16, True), (0, 17, False), (0, 22, False), (6, 22, True), (6, 16, False)])
def test_weekly_frequency_uses_the_users_first_day_of_week(user, week_start, day, expected):
    p = opt_in(user, email_frequency="weekly", week_start=week_start)
    assert is_due(p, local(p, 2026, 3, day, 9)) is expected


# ------------------------------------------------------------ sending
def test_sends_one_email_with_the_right_content(user, make_task, settings):
    settings.SITE_URL = "https://lifeos.example.com"
    opt_in(user)
    t = make_task(user, title="Renew passport", due_date=dt.date(2026, 3, 18))
    res = send_due_digests(now=WED_9)
    assert (res.sent, res.considered) == (1, 1) and len(mail.outbox) == 1
    m = mail.outbox[0]
    assert m.to == [user.email] and m.from_email == settings.DEFAULT_FROM_EMAIL
    assert m.subject == "[LifeOS] 1 thing to look at (1 urgent)"
    assert "Renew passport" in m.body and "due today" in m.body and "Wednesday 18 March" in m.body
    assert f"https://lifeos.example.com{reverse('planner:task_update', args=[t.pk])}" in m.body
    assert "https://lifeos.example.com/accounts/settings/" in m.body and "turned on email reminders" in m.body


def test_running_twice_the_same_day_sends_once(user, make_task):
    opt_in(user); make_task(user, title="x", due_date=dt.date(2026, 3, 18))
    send_due_digests(now=WED_9)
    again = send_due_digests(now=WED_9 + dt.timedelta(hours=3))
    assert len(mail.outbox) == 1 and again.sent == 0 and again.skipped_not_due == 1


def test_next_day_sends_again(user, make_task):
    opt_in(user); make_task(user, title="x", due_date=dt.date(2026, 3, 18)); make_task(user, title="y", due_date=dt.date(2026, 3, 19))
    send_due_digests(now=WED_9); send_due_digests(now=WED_9 + dt.timedelta(days=1))
    assert len(mail.outbox) == 2


def test_nothing_to_say_sends_nothing_but_consumes_the_day(user, make_task):
    p = opt_in(user)
    res = send_due_digests(now=WED_9)
    assert res.skipped_empty == 1 and mail.outbox == []
    make_task(user, title="Created later", due_date=dt.date(2026, 3, 18))
    send_due_digests(now=WED_9 + dt.timedelta(hours=4))
    assert mail.outbox == []                                                  # no surprise afternoon email
    p.refresh_from_db(); assert p.last_digest_on == dt.date(2026, 3, 18)
    send_due_digests(now=WED_9 + dt.timedelta(days=1))                        # next morning it is overdue, so it is reported
    assert len(mail.outbox) == 1 and "Created later" in mail.outbox[0].body


def test_disabled_and_inactive_users_are_never_emailed(user, other_user, make_task):
    for u in (user, other_user):
        make_task(u, title="t", due_date=dt.date(2026, 3, 18))
    p = opt_in(user, reminders_enabled=False)
    opt_in(other_user); other_user.is_active = False; other_user.save()
    res = send_due_digests(now=WED_9)
    assert mail.outbox == [] and res.considered == 0
    p.reminders_enabled = True; p.email_reminders = False; p.save()
    assert send_due_digests(now=WED_9).considered == 0


def test_hour_and_quiet_hours_respected_end_to_end(user, make_task):
    opt_in(user, email_digest_hour=7, quiet_start_hour=22, quiet_end_hour=9)
    make_task(user, title="x", due_date=dt.date(2026, 3, 18))
    send_due_digests(now=dt.datetime(2026, 3, 18, 8, 0, tzinfo=UTC))
    assert mail.outbox == []
    send_due_digests(now=dt.datetime(2026, 3, 18, 9, 0, tzinfo=UTC))
    assert len(mail.outbox) == 1


def test_each_user_is_judged_in_their_own_timezone(user, other_user, make_task):
    """At 20:30 UTC on 17 March it is 09:30 on 18 March in Auckland but still the evening of the 17th in London."""
    opt_in(user, timezone="Pacific/Auckland", email_digest_hour=9)
    opt_in(other_user, timezone="Europe/London", email_digest_hour=9)
    make_task(user, title="Auckland task", due_date=dt.date(2026, 3, 18)); make_task(other_user, title="London task", due_date=dt.date(2026, 3, 18))
    send_due_digests(now=dt.datetime(2026, 3, 17, 20, 30, tzinfo=UTC))
    assert len(mail.outbox) == 2                                              # both are past 09:00 local, on different dates
    by = {m.to[0]: m for m in mail.outbox}
    assert "Wednesday 18 March" in by[user.email].body and "due today" in by[user.email].body
    assert "Tuesday 17 March" in by[other_user.email].body and "due tomorrow" in by[other_user.email].body
    user.profile.refresh_from_db(); other_user.profile.refresh_from_db()
    assert user.profile.last_digest_on == dt.date(2026, 3, 18) and other_user.profile.last_digest_on == dt.date(2026, 3, 17)


def test_a_delivery_failure_releases_the_claim_so_the_next_run_retries(user, make_task, monkeypatch):
    from django.core.mail import EmailMessage
    p = opt_in(user); make_task(user, title="x", due_date=dt.date(2026, 3, 18))
    monkeypatch.setattr(EmailMessage, "send", lambda self, fail_silently=False: (_ for _ in ()).throw(OSError("smtp down")))
    res = send_due_digests(now=WED_9)
    assert res.failed == 1 and res.sent == 0 and mail.outbox == []
    p.refresh_from_db(); assert p.last_digest_on is None
    monkeypatch.undo()
    assert send_due_digests(now=WED_9 + dt.timedelta(minutes=30)).sent == 1 and len(mail.outbox) == 1


def test_one_users_failure_does_not_stop_the_others(user, other_user, make_task, monkeypatch):
    from django.core.mail import EmailMessage
    for u in (user, other_user):
        opt_in(u); make_task(u, title=f"t-{u.pk}", due_date=dt.date(2026, 3, 18))
    real = EmailMessage.send
    def flaky(self, fail_silently=False):
        if self.to == [user.email]:
            raise OSError("boom")
        return real(self, fail_silently=fail_silently)
    monkeypatch.setattr(EmailMessage, "send", flaky)
    res = send_due_digests(now=WED_9)
    assert (res.failed, res.sent) == (1, 1) and [m.to[0] for m in mail.outbox] == [other_user.email]


def test_dry_run_sends_and_changes_nothing(user, make_task):
    p = opt_in(user); make_task(user, title="x", due_date=dt.date(2026, 3, 18))
    res = send_due_digests(now=WED_9, dry_run=True)
    assert res.sent == 1 and mail.outbox == []
    p.refresh_from_db(); assert p.last_digest_on is None
    assert send_due_digests(now=WED_9).sent == 1


# --------------------------------------------------------- content & safety
def test_only_titles_the_user_already_sees_are_included_and_never_private_text(user, make_task):
    from apps.reflections.models import JournalEntry, WeeklyReview
    opt_in(user)
    make_task(user, title="Visible title", description="TASKDESCRIPTIONMARKER", due_date=dt.date(2026, 3, 18))
    JournalEntry.objects.create(owner=user, date=dt.date(2026, 3, 17), title="JOURNALTITLEMARKER", body="JOURNALBODYMARKER")
    WeeklyReview.objects.create(owner=user, week_start=dt.date(2026, 3, 9), lessons="REVIEWMARKER")
    send_due_digests(now=dt.datetime(2026, 3, 20, 9, 0, tzinfo=UTC))           # a Friday: the review nudge is also active
    body = mail.outbox[0].body
    assert "Visible title" in body and "Weekly review" in body
    for marker in ("TASKDESCRIPTIONMARKER", "JOURNALTITLEMARKER", "JOURNALBODYMARKER", "REVIEWMARKER"):
        assert marker not in body


def test_hostile_titles_cannot_inject_headers_or_recipients(user, make_task):
    opt_in(user)
    make_task(user, title="Pay\r\nBcc: evil@example.com\r\nSubject: hacked", due_date=dt.date(2026, 3, 18))
    send_due_digests(now=WED_9)
    m = mail.outbox[0]
    assert m.bcc == [] and m.cc == [] and m.to == [user.email]
    assert "evil@example.com" not in m.subject and "\n" not in m.subject and "\r" not in m.subject
    assert m.subject == "[LifeOS] 1 thing to look at (1 urgent)"
    raw = m.message().as_string()
    assert "\nBcc:" not in raw.split("\n\n", 1)[0]                              # only in the body, never in the header block


def test_each_email_contains_only_that_users_items(user, other_user, make_task):
    for u, title in ((user, "Alice-only item"), (other_user, "Bob-only item")):
        opt_in(u); make_task(u, title=title, due_date=dt.date(2026, 3, 18))
    send_due_digests(now=WED_9)
    by = {m.to[0]: m.body for m in mail.outbox}
    assert "Alice-only item" in by[user.email] and "Bob-only item" not in by[user.email]
    assert "Bob-only item" in by[other_user.email] and "Alice-only item" not in by[other_user.email]


def test_subject_pluralises_and_omits_urgent_when_none(user, make_task):
    items = [{"level": 1, "title": "a", "detail": "d", "url": "/x/"}, {"level": 1, "title": "b", "detail": "d", "url": "/y/"}]
    subject, body = render_digest(user, items, dt.date(2026, 3, 18))
    assert subject == "[LifeOS] 2 things to look at" and "Coming up" in body and "Needs attention now" not in body


# -------------------------------------------------------- command & parallel
def test_command_reports_a_summary_and_supports_dry_run(user, make_task, monkeypatch):
    from django.utils import timezone
    monkeypatch.setattr(timezone, "now", lambda: WED_9)
    opt_in(user); make_task(user, title="x", due_date=dt.date(2026, 3, 18))
    out = StringIO(); call_command("send_reminders", "--dry-run", stdout=out)
    assert "[dry run]" in out.getvalue() and "sent 1" in out.getvalue() and mail.outbox == []
    out = StringIO(); call_command("send_reminders", stdout=out)
    assert "sent 1" in out.getvalue() and len(mail.outbox) == 1
    out = StringIO(); call_command("send_reminders", stdout=out)
    assert "sent 0" in out.getvalue() and len(mail.outbox) == 1


@pytest.mark.django_db(transaction=True)
def test_parallel_runs_never_send_duplicates(user):
    from apps.planner.models import Task
    from tests.test_finance_concurrency import run_parallel
    opt_in(user)
    Task.objects.create(owner=user, title="x", due_date=dt.date(2026, 3, 18))
    results = run_parallel(lambda i: send_due_digests(now=WED_9), 6)
    assert all(not isinstance(r, Exception) for r in results), results
    assert len(mail.outbox) == 1 and sum(r.sent for r in results) == 1


# ----------------------------------------------------------- settings UI
BASE = {"section": "prefs", "prefs-currency": "GBP", "prefs-timezone": "Europe/London", "prefs-date_format": "d/m/Y", "prefs-week_start": "0",
        "prefs-daily_available_minutes": "480", "prefs-reminders_enabled": "on", "prefs-reminder_lead_days": "3", "prefs-theme": "light"}


def test_settings_save_email_preferences(auth_client, user):
    r = auth_client.post(reverse("accounts:settings"), {**BASE, "prefs-email_reminders": "on", "prefs-email_frequency": "weekdays", "prefs-email_digest_hour": "7",
                                                       "prefs-quiet_start_hour": "22", "prefs-quiet_end_hour": "6"})
    assert r.status_code == 302
    p = user.profile; p.refresh_from_db()
    assert (p.email_reminders, p.email_frequency, p.email_digest_hour, p.quiet_start_hour, p.quiet_end_hour) == (True, "weekdays", 7, 22, 6)


@pytest.mark.parametrize("over", [{"prefs-email_digest_hour": "24"}, {"prefs-email_digest_hour": "-1"}, {"prefs-email_frequency": "hourly"},
                                  {"prefs-quiet_start_hour": "22"}, {"prefs-quiet_end_hour": "6"}, {"prefs-quiet_start_hour": "8", "prefs-quiet_end_hour": "8"},
                                  {"prefs-quiet_start_hour": "25", "prefs-quiet_end_hour": "6"}])
def test_settings_reject_invalid_email_preferences(auth_client, user, over):
    r = auth_client.post(reverse("accounts:settings"), {**BASE, "prefs-email_reminders": "on", "prefs-email_frequency": "daily", "prefs-email_digest_hour": "8", **over})
    assert r.status_code == 200
    user.profile.refresh_from_db(); assert user.profile.email_reminders is False


def test_posts_that_omit_the_new_fields_keep_existing_values(auth_client, user):
    opt_in(user, email_frequency="weekly", email_digest_hour=6)
    auth_client.post(reverse("accounts:settings"), {**BASE, "prefs-email_reminders": "on"})
    user.profile.refresh_from_db()
    assert user.profile.email_frequency == "weekly" and user.profile.email_digest_hour == 6 and user.profile.email_reminders is True


def test_turning_email_off_stops_future_digests(auth_client, user, make_task):
    opt_in(user); make_task(user, title="x", due_date=dt.date(2026, 3, 18))
    auth_client.post(reverse("accounts:settings"), BASE)                       # checkbox absent = off
    assert send_due_digests(now=WED_9).sent == 0 and mail.outbox == []


def test_email_settings_are_in_the_data_export(auth_client, user):
    import json
    opt_in(user, email_frequency="weekly", quiet_start_hour=22, quiet_end_hour=7)
    prof = json.loads(auth_client.get(reverse("accounts:export")).content)["profile"]
    assert prof["email_reminders"] is True and prof["email_frequency"] == "weekly" and prof["quiet_start_hour"] == 22 and "last_digest_on" not in prof
