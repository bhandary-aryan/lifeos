"""Automated accessibility checks: colour contrast (WCAG AA) computed from the real theme/CSS, plus an HTML lint over rendered pages."""
import datetime as dt
import re
from decimal import Decimal as D
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.urls import reverse

from apps.core.services import local_today

ROOT = Path(__file__).resolve().parent.parent
AA = 4.5


# ------------------------------------------------------------- contrast maths
def lum(h):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg, bg, alpha):
    fg, bg = fg.lstrip("#"), bg.lstrip("#")
    return "#" + "".join("%02x" % round(int(fg[i:i + 2], 16) * alpha + int(bg[i:i + 2], 16) * (1 - alpha)) for i in (0, 2, 4))


def load_themes():
    src = (ROOT / "tailwind.config.js").read_text()
    out = {}
    for name in ("lifeos-light", "lifeos-dark"):
        block = src[src.index(f'"{name}"'):]
        block = block[:block.index("}")]
        out[name] = dict(re.findall(r'"([a-z0-9-]+)":\s*"(#[0-9a-fA-F]{6})"', block))
    return out


THEMES = load_themes()
BACKGROUNDS = ("base-100", "base-200")
TEXT_ROLES = ("base-content", "primary", "secondary", "accent", "success", "warning", "error", "info")
CONTENT_ROLES = ("primary", "secondary", "accent", "neutral", "success", "warning", "error", "info")


def test_both_themes_define_every_role():
    for name, t in THEMES.items():
        for role in TEXT_ROLES + tuple(f"{r}-content" for r in CONTENT_ROLES) + BACKGROUNDS:
            assert role in t, (name, role)


@pytest.mark.parametrize("theme", ["lifeos-light", "lifeos-dark"])
@pytest.mark.parametrize("bg", BACKGROUNDS)
@pytest.mark.parametrize("role", TEXT_ROLES)
def test_coloured_text_meets_aa_on_page_backgrounds(theme, bg, role):
    t = THEMES[theme]
    assert ratio(t[role], t[bg]) >= AA, f"{role} on {bg} in {theme}: {ratio(t[role], t[bg]):.2f}"


@pytest.mark.parametrize("theme", ["lifeos-light", "lifeos-dark"])
@pytest.mark.parametrize("role", CONTENT_ROLES)
def test_text_on_coloured_surfaces_meets_aa(theme, role):
    t = THEMES[theme]
    assert ratio(t[f"{role}-content"], t[role]) >= AA, f"{role}-content on {role} in {theme}: {ratio(t[f'{role}-content'], t[role]):.2f}"


def muted_floors():
    css = (ROOT / "static/src/input.css").read_text()
    return {int(n): float(a) for n, a in re.findall(r"\.text-base-content\\/(\d+)\s*\{\s*color:\s*oklch\(var\(--bc\)\s*/\s*([0-9.]+)\)", css)}


def test_muted_text_floors_exist_for_every_step_used_in_templates():
    used = set()
    for path in list((ROOT / "templates").rglob("*.html")):
        used |= {int(n) for n in re.findall(r"text-base-content/(\d+)", path.read_text())}
    used -= {20}                         # /20 is decorative only (out-of-month calendar dates)
    assert used <= set(muted_floors()), f"muted text steps without an accessible floor: {sorted(used - set(muted_floors()))}"


@pytest.mark.parametrize("theme", ["lifeos-light", "lifeos-dark"])
@pytest.mark.parametrize("bg", BACKGROUNDS)
def test_muted_text_floors_meet_aa(theme, bg):
    t = THEMES[theme]
    for step, alpha in muted_floors().items():
        eff = blend(t["base-content"], t[bg], alpha)
        assert ratio(eff, t[bg]) >= AA, f"text-base-content/{step} (alpha {alpha}) on {bg} in {theme}: {ratio(eff, t[bg]):.2f}"


def test_built_stylesheet_contains_the_overrides():
    built = (ROOT / "static/css/app.css").read_text()
    for step, alpha in muted_floors().items():
        assert f"text-base-content\\/{step}{{color:oklch(var(--bc)/{str(alpha).lstrip('0')})}}" in built, step
    assert "prefers-reduced-motion" in built and ":focus-visible" in built


# ------------------------------------------------------------------ HTML lint
class Lint(HTMLParser):
    VOID = {"input", "img", "br", "hr", "meta", "link"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.problems, self.labels_for, self.controls, self.stack = [], set(), [], []
        self.h1 = 0
        self.has_main = self.has_lang = self.has_skip = False
        self.main_id = None
        self.buttons = []                      # (tag, attrs, text_parts, has_icon)
        self.in_label = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html" and a.get("lang"):
            self.has_lang = True
        if tag == "h1":
            self.h1 += 1
        if tag == "main":
            self.has_main = True
            self.main_id = a.get("id")
        if tag == "a" and a.get("href") == "#main":
            self.has_skip = True
        if tag == "label":
            self.in_label += 1
            if a.get("for"):
                self.labels_for.add(a["for"])
        if tag == "img" and "alt" not in a:
            self.problems.append(f"<img src={a.get('src')}> has no alt attribute")
        if tag in ("input", "select", "textarea"):
            t = a.get("type", "text")
            if t not in ("hidden", "submit", "button", "reset"):
                self.controls.append({"tag": tag, "id": a.get("id"), "name": a.get("name"), "aria": a.get("aria-label") or a.get("aria-labelledby"),
                                      "in_label": self.in_label > 0, "type": t})
        if tag in ("button", "a") and not (tag == "a" and not a.get("href")):
            self.buttons.append({"tag": tag, "aria": a.get("aria-label") or a.get("title"), "text": [], "icon": False, "attrs": a})
            self.stack.append(len(self.buttons) - 1)
        if tag == "i" and a.get("data-lucide") and self.stack:
            self.buttons[self.stack[-1]]["icon"] = True
        if tag == "span" and "sr-only" in (a.get("class") or "") and self.stack:
            self.buttons[self.stack[-1]]["sr"] = True

    def handle_endtag(self, tag):
        if tag == "label":
            self.in_label -= 1
        if tag in ("button", "a") and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack and data.strip():
            self.buttons[self.stack[-1]]["text"].append(data.strip())

    def report(self):
        out = list(self.problems)
        if not self.has_lang: out.append("<html> has no lang")
        if not self.has_main: out.append("no <main> landmark")
        if self.has_skip and self.main_id != "main": out.append("skip link target #main does not exist")
        if self.h1 != 1: out.append(f"expected exactly one <h1>, found {self.h1}")
        for c in self.controls:
            if not (c["aria"] or c["in_label"] or (c["id"] and c["id"] in self.labels_for)):
                out.append(f"unlabelled {c['tag']} name={c['name']} id={c['id']}")
        for b in self.buttons:
            if b["icon"] and not b["text"] and not b["aria"] and not b.get("sr"):
                out.append(f"icon-only {b['tag']} with no accessible name: {b['attrs']}")
        return out


def lint(html, require_skip=True):
    p = Lint(); p.feed(html)
    out = p.report()
    if require_skip and not p.has_skip:
        out.append("no skip-to-content link")
    return out


@pytest.fixture
def populated(user, make_goal, make_milestone, make_task, make_habit, log_habit, make_account, make_tx, make_debt, make_savings):
    from apps.finance.models import Budget, Category, SavingsContribution, Transaction
    from apps.future_plans.models import FuturePlan, PlanAction
    from apps.interests.models import InterestItem
    from apps.reflections.models import JournalEntry, WeeklyReview
    t = local_today(user)
    g = make_goal(user, title="Goal", progress_mode="task"); m = make_milestone(g); make_task(user, goal=g, milestone=m, due_date=t, estimated_minutes=30)
    h = make_habit(user); log_habit(h, t)
    a = make_account(user, opening="100"); b = make_account(user, "Savings")
    make_tx(user, "income", "50", a); make_tx(user, "transfer", "10", a, destination_account=b)
    debt = make_debt(user); sg = make_savings(user, target_date=t + dt.timedelta(days=100))
    SavingsContribution.objects.create(owner=user, goal=sg, date=t, amount=D("5"))
    Budget.objects.create(owner=user, category=Category.objects.get(owner=user, name="Groceries"), year=t.year, month=t.month, currency="GBP", limit_amount=D("50"))
    plan = FuturePlan.objects.create(owner=user, title="Plan", estimated_cost=D("500"), savings_goal=sg, target_date=t + dt.timedelta(days=100))
    PlanAction.objects.create(owner=user, plan=plan, title="Step", due_date=t)
    item = InterestItem.objects.create(owner=user, name="Thing", tags=["x"], is_bucket_list=True, target_date=t + dt.timedelta(days=5))
    entry = JournalEntry.objects.create(owner=user, date=t, body="words")
    rev = WeeklyReview.objects.create(owner=user, week_start=t - dt.timedelta(days=t.weekday()), lessons="x")
    return dict(g=g, h=h, debt=debt, sg=sg, plan=plan, item=item, entry=entry, tx=Transaction.objects.first(), rev=rev)


def page_urls(d):
    return [reverse(n) for n in (
        "dashboard:home", "dashboard:reminders", "accounts:settings", "accounts:delete", "goals:list", "goals:create", "goals:areas", "planner:today",
        "planner:week", "planner:upcoming", "planner:all", "planner:task_create", "planner:habits", "planner:habit_create", "finance:overview", "finance:reports",
        "finance:accounts", "finance:categories", "finance:transactions", "finance:transaction_create", "finance:budgets", "finance:budget_create", "finance:debts",
        "finance:debt_create", "finance:debt_planner", "finance:savings", "finance:savings_create", "future_plans:list", "future_plans:create",
        "future_plans:affordability", "interests:list", "interests:create", "reflections:list", "reflections:journal", "reflections:journal_create",
        "reflections:insights")] + [
        reverse("goals:detail", args=[d["g"].pk]), reverse("goals:update", args=[d["g"].pk]), reverse("goals:delete", args=[d["g"].pk]),
        reverse("planner:habit_detail", args=[d["h"].pk]), reverse("finance:debt_detail", args=[d["debt"].pk]), reverse("finance:savings_detail", args=[d["sg"].pk]),
        reverse("finance:transaction_update", args=[d["tx"].pk]), reverse("future_plans:detail", args=[d["plan"].pk]), reverse("interests:detail", args=[d["item"].pk]),
        reverse("interests:update", args=[d["item"].pk]), reverse("reflections:journal_detail", args=[d["entry"].pk]),
        reverse("reflections:review_week", args=[d["rev"].week_start.isoformat()])]


def test_rendered_pages_pass_the_html_lint(auth_client, populated):
    failures = {}
    for url in page_urls(populated):
        r = auth_client.get(url, follow=True)
        assert r.status_code == 200, url
        problems = lint(r.content.decode())
        if problems:
            failures[url] = problems
    assert not failures, "\n".join(f"{u}: {p}" for u, p in failures.items())


def test_auth_pages_pass_the_html_lint(client):
    for name in ("accounts:login", "accounts:register", "accounts:password_reset", "accounts:password_reset_done", "accounts:password_reset_complete"):
        problems = lint(client.get(reverse(name)).content.decode(), require_skip=False)
        assert not problems, (name, problems)


def test_onboarding_pages_pass_the_html_lint(auth_client):
    for step in (1, 2, 3):
        problems = lint(auth_client.get(reverse("accounts:onboarding", kwargs={"step": step})).content.decode(), require_skip=False)
        assert not problems, (step, problems)


def test_lint_actually_detects_problems():
    bad = '<html><body><main id="main"><h1>a</h1><h1>b</h1><input name="x"><img src="a.png"><button><i data-lucide="x"></i></button></main></body></html>'
    found = lint(bad)
    assert any("unlabelled input" in p for p in found) and any("no alt" in p for p in found) and any("icon-only button" in p for p in found)
    assert any("exactly one <h1>" in p for p in found) and any("no lang" in p for p in found) and any("skip" in p for p in found)
    good = '<html lang="en"><body><a href="#main">Skip</a><main id="main"><h1>a</h1><label for="x">X</label><input id="x"><img src="a" alt=""><button aria-label="Close"><i data-lucide="x"></i></button></main></body></html>'
    assert lint(good) == []


def test_keyboard_focus_style_and_reduced_motion_are_defined():
    css = (ROOT / "static/src/input.css").read_text()
    assert ":focus-visible" in css and "outline" in css and "prefers-reduced-motion: reduce" in css
