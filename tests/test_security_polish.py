import csv
import datetime as dt
import io

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.services import local_today

pytestmark = pytest.mark.django_db
LOGIN = reverse("accounts:login")
RESET = reverse("accounts:password_reset")


def attempt(client, email, password, **extra):
    return client.post(LOGIN, {"username": email, "password": password}, **extra)


# ============================================================ login throttling
def test_five_failures_then_even_the_right_password_is_refused(client, user, password):
    for _ in range(5):
        assert attempt(client, user.email, "wrong").status_code == 200
    r = attempt(client, user.email, password)
    assert r.status_code == 429 and int(r["Retry-After"]) > 0 and "Too many sign-in attempts" in r.content.decode()
    assert "_auth_user_id" not in client.session


def test_failures_below_the_limit_do_not_block(client, user, password):
    for _ in range(4):
        attempt(client, user.email, "wrong")
    assert attempt(client, user.email, password).status_code == 302


def test_success_resets_the_counter(client, user, password):
    for _ in range(4):
        attempt(client, user.email, "wrong")
    assert attempt(client, user.email, password).status_code == 302
    client.post(reverse("accounts:logout"))
    for _ in range(4):
        assert attempt(client, user.email, "wrong").status_code == 200
    assert attempt(client, user.email, password).status_code == 302


def test_lockout_is_per_account_and_per_address(client, user, other_user, password):
    for _ in range(5):
        attempt(client, user.email, "wrong")
    assert attempt(client, user.email, password).status_code == 429
    assert attempt(client, other_user.email, password).status_code == 302                       # same address, another account: fine
    other_ip = Client()
    assert attempt(other_ip, user.email, password, REMOTE_ADDR="203.0.113.9").status_code == 302   # someone else's address cannot be locked out by it


def test_attacker_cannot_lock_the_owner_out_from_elsewhere(user, password):
    attacker = Client()
    for _ in range(8):
        attempt(attacker, user.email, "guess", REMOTE_ADDR="198.51.100.7")
    assert attempt(attacker, user.email, password, REMOTE_ADDR="198.51.100.7").status_code == 429
    owner = Client()
    assert attempt(owner, user.email, password, REMOTE_ADDR="203.0.113.50").status_code == 302


def test_one_address_trying_many_accounts_is_stopped(client, user, password):
    for i in range(20):
        attempt(client, f"nobody{i}@example.com", "x")
    assert attempt(client, user.email, password).status_code == 429


def test_lockout_expires_after_the_window(client, user, password, monkeypatch):
    for _ in range(5):
        attempt(client, user.email, "wrong")
    assert attempt(client, user.email, password).status_code == 429
    later = timezone.now() + dt.timedelta(minutes=16)
    monkeypatch.setattr(timezone, "now", lambda: later)
    assert attempt(client, user.email, password).status_code == 302


def test_email_key_ignores_case_and_whitespace(client, user, password):
    for variant in (user.email.upper(), f" {user.email} ", user.email, user.email.title(), user.email.upper()):
        attempt(client, variant, "wrong")
    assert attempt(client, user.email, password).status_code == 429


def test_unknown_and_known_emails_look_identical(client, user):
    known = attempt(Client(), user.email, "wrong", REMOTE_ADDR="192.0.2.1")
    unknown = attempt(Client(), "ghost@example.com", "wrong", REMOTE_ADDR="192.0.2.2")
    assert known.status_code == unknown.status_code == 200
    assert known.content.decode().count("Please enter a correct") == unknown.content.decode().count("Please enter a correct") >= 1
    for i in range(5):
        attempt(client, "ghost@example.com", "wrong")
    blocked = attempt(client, "ghost@example.com", "wrong")
    assert blocked.status_code == 429                                          # unknown accounts are throttled exactly like real ones


def test_spoofed_forwarded_header_is_ignored_by_default(client, user, password):
    for i in range(5):
        attempt(client, user.email, "wrong", HTTP_X_FORWARDED_FOR=f"10.9.9.{i}")
    assert attempt(client, user.email, password, HTTP_X_FORWARDED_FOR="10.8.8.8").status_code == 429


def test_forwarded_header_used_only_when_explicitly_trusted(client, user, password, settings):
    settings.THROTTLE_TRUST_X_FORWARDED_FOR = True
    for _ in range(5):
        attempt(client, user.email, "wrong", HTTP_X_FORWARDED_FOR="203.0.113.5, 10.0.0.1")
    assert attempt(client, user.email, password, HTTP_X_FORWARDED_FOR="203.0.113.5").status_code == 429
    assert attempt(Client(), user.email, password, HTTP_X_FORWARDED_FOR="203.0.113.6").status_code == 302


def test_get_requests_are_never_throttled(client, user):
    for _ in range(25):
        attempt(client, user.email, "wrong")
    assert client.get(LOGIN).status_code == 200


# ---------------------------------------------------------- reset throttling
def test_password_reset_requests_are_rate_limited_without_revealing_accounts(client, user):
    for _ in range(5):
        assert client.post(RESET, {"email": user.email}).status_code == 302
    assert len(mail.outbox) == 5
    r = client.post(RESET, {"email": user.email})
    assert r.status_code == 429 and "Too many reset requests" in r.content.decode() and len(mail.outbox) == 5


def test_unknown_email_reset_requests_count_too_and_send_nothing(client):
    for _ in range(5):
        assert client.post(RESET, {"email": "ghost@example.com"}).status_code == 302
    assert client.post(RESET, {"email": "ghost@example.com"}).status_code == 429 and mail.outbox == []


# ================================================================ error pages
@pytest.fixture
def prod_like(settings):
    settings.DEBUG = False


def test_404_is_friendly_and_generic(client, prod_like):
    r = client.get("/definitely/not/here/")
    h = r.content.decode()
    assert r.status_code == 404 and "We can&#x27;t find that page" in h or "We can't find that page" in h
    assert "Traceback" not in h and "definitely" not in h and 'href="/"' in h


def test_missing_and_foreign_objects_are_indistinguishable(user, other_user, make_goal, prod_like):
    theirs = make_goal(other_user, title="BobPrivateGoalTitle")
    alice = Client(); alice.force_login(user)
    missing = alice.get(reverse("goals:detail", args=[999999]))
    foreign = alice.get(reverse("goals:detail", args=[theirs.pk]))
    assert missing.status_code == foreign.status_code == 404
    assert missing.content == foreign.content and b"BobPrivateGoalTitle" not in foreign.content


def test_500_page_hides_internals_and_never_fires_twice(user, monkeypatch, prod_like):
    def boom(*a, **k):
        raise RuntimeError("SECRET-INTERNAL-DETAIL /home/claude/lifeos")
    monkeypatch.setattr("apps.dashboard.views.build_dashboard_summary", boom)
    c = Client(raise_request_exception=False); c.force_login(user)
    r = c.get(reverse("dashboard:home"))
    h = r.content.decode()
    assert r.status_code == 500 and "Something went wrong on our side" in h and "Your data has not been changed" in h
    assert "SECRET-INTERNAL-DETAIL" not in h and "/home/claude" not in h and "Traceback" not in h


def test_csrf_failure_shows_friendly_page_without_technical_reason(user, prod_like):
    c = Client(enforce_csrf_checks=True); c.force_login(user)
    r = c.post(reverse("planner:quick_add"), {"title": "x"})
    h = r.content.decode()
    assert r.status_code == 403 and "session may have expired" in h
    assert "CSRF" not in h and "Forbidden (" not in h


def test_bad_host_header_gets_a_clean_400(client, prod_like):
    r = client.get("/accounts/login/", HTTP_HOST="evil.example")
    assert r.status_code == 400 and "didn&#x27;t make sense" in r.content.decode() or "didn't make sense" in r.content.decode()


def test_error_pages_have_no_template_dependencies_on_the_session(client, prod_like):
    """They must render for anonymous visitors too (no nav, no user context)."""
    h = client.get("/nope/").content.decode()
    assert "<nav" not in h and "Skip to content" not in h and "<main" in h


def test_error_pages_pass_the_html_lint(client, prod_like):
    from tests.test_accessibility import lint
    assert lint(client.get("/nope/").content.decode(), require_skip=False) == []


# ================================================================= CSV exports
def rows(resp):
    return list(csv.reader(io.StringIO(resp.content.decode())))


def test_each_csv_export_has_headers_only_own_rows_and_attachment_header(auth_client, user, other_user, make_task, make_goal, make_habit, log_habit):
    make_task(user, title="Mine task"); make_goal(user, title="Mine goal"); h = make_habit(user, name="Mine habit"); log_habit(h, local_today(user))
    from apps.reflections.models import JournalEntry
    JournalEntry.objects.create(owner=user, date=local_today(user), title="Mine entry", body="Mine body")
    make_task(other_user, title="BOB task"); make_goal(other_user, title="BOB goal"); JournalEntry.objects.create(owner=other_user, date=local_today(user), body="BOB body")
    expected = {"tasks": ("title", "Mine task"), "goals": ("title", "Mine goal"), "habit_logs": ("habit", "Mine habit"), "journal": ("date", None)}
    for kind, (header, value) in expected.items():
        r = auth_client.get(reverse("accounts:export_csv", args=[kind]))
        assert r.status_code == 200 and r["Content-Type"].startswith("text/csv") and f"lifeos-{kind}.csv" in r["Content-Disposition"]
        data = rows(r)
        assert data[0][0] == header and len(data) == 2 and "BOB" not in r.content.decode()
        if value:
            assert data[1][0] == value


def test_csv_exports_neutralise_formulas_in_every_text_column(auth_client, user, make_task, make_goal):
    evil = '=HYPERLINK("http://evil.example")'
    make_task(user, title=evil, description="+SUM(1,1)"); make_goal(user, title="@cmd", description="-1+1")
    t = rows(auth_client.get(reverse("accounts:export_csv", args=["tasks"])))[1]
    g = rows(auth_client.get(reverse("accounts:export_csv", args=["goals"])))[1]
    assert t[0].startswith("'=") and t[-1].startswith("'+") and g[0].startswith("'@") and g[-1].startswith("'-")


def test_unknown_csv_kind_and_anonymous(auth_client):
    assert auth_client.get(reverse("accounts:export_csv", args=["secrets"])).status_code == 404
    r = Client().get(reverse("accounts:export_csv", args=["tasks"]))             # a genuinely anonymous client (auth_client shares `client`)
    assert r.status_code == 302 and r.url.startswith(LOGIN)


def test_settings_page_links_to_all_exports(auth_client):
    h = auth_client.get(reverse("accounts:settings")).content.decode()
    for kind in ("tasks", "goals", "habit_logs", "journal"):
        assert reverse("accounts:export_csv", args=[kind]) in h
    assert reverse("finance:transactions_export") in h and reverse("accounts:export") in h


# ============================================================== demo seeder
def test_seeder_refuses_to_run_without_debug(db, settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("seed_demo")
    from django.contrib.auth import get_user_model
    assert not get_user_model().objects.filter(email__endswith="@demo.lifeos.invalid").exists()


def test_seeder_builds_a_clearly_labelled_demo_account(db, settings, user, make_goal):
    settings.DEBUG = True
    real = make_goal(user, title="Real goal")
    call_command("seed_demo", password="demo-Pass-12345")
    from django.contrib.auth import get_user_model
    demo = get_user_model().objects.get(email="demo@demo.lifeos.invalid")
    from apps.finance.models import Account, Debt, SavingsGoal, Transaction
    from apps.future_plans.models import FuturePlan
    from apps.goals.models import Goal
    from apps.interests.models import InterestItem
    from apps.planner.models import Habit, Task
    for model, field in ((Goal, "title"), (Task, "title"), (Habit, "name"), (Account, "name"), (Debt, "creditor"), (SavingsGoal, "name"), (FuturePlan, "title"), (InterestItem, "name")):
        names = list(model.objects.filter(owner=demo).values_list(field, flat=True))
        assert names and all(n.startswith("[Demo] ") for n in names), (model.__name__, names)
    assert Transaction.objects.filter(owner=demo).count() > 10
    assert "Demo" in demo.profile.display_name
    real.refresh_from_db()
    assert real.title == "Real goal" and Goal.objects.filter(owner=user).count() == 1            # real accounts untouched


def test_seeder_is_idempotent(db, settings):
    settings.DEBUG = True
    from apps.goals.models import Goal
    call_command("seed_demo", password="demo-Pass-12345")
    first = Goal.objects.count()
    call_command("seed_demo", password="demo-Pass-12345")
    assert Goal.objects.count() == first


def test_demo_account_shows_banner_and_real_accounts_do_not(db, settings, user):
    auth_client = Client(); auth_client.force_login(user)
    settings.DEBUG = True
    call_command("seed_demo", password="demo-Pass-12345")
    demo_client = Client()
    assert demo_client.login(username="demo@demo.lifeos.invalid", password="demo-Pass-12345")
    h = demo_client.get(reverse("dashboard:home")).content.decode()
    assert "This is a demo account" in h and "[Demo]" in h
    assert "This is a demo account" not in auth_client.get(reverse("dashboard:home")).content.decode()


def test_demo_data_is_internally_consistent(db, settings):
    settings.DEBUG = True
    call_command("seed_demo", password="demo-Pass-12345")
    from django.contrib.auth import get_user_model
    from apps.finance import services as fin
    from apps.finance.models import Debt
    demo = get_user_model().objects.get(email="demo@demo.lifeos.invalid")
    t = local_today(demo)
    s = fin.month_summary(demo, "GBP", t.year, t.month)
    assert s["income"] > 0 and s["interest_paid"] == 15 and s["principal_repaid"] == 105
    debt = Debt.objects.get(owner=demo)
    assert fin.outstanding_principal(debt) == 4000 - 3 * 105
    c = Client(); c.force_login(demo)
    for name in ("dashboard:home", "finance:overview", "reflections:insights", "goals:list", "planner:habits", "future_plans:list", "interests:list"):
        assert c.get(reverse(name)).status_code == 200, name
