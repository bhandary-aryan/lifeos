import datetime as dt
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.models import CURRENCY_CHOICES
from apps.core.services import local_today, parse_date
from apps.core.views import confirm_and_delete
from apps.planner.services import week_start_for

from . import insights as ins
from .forms import PROMPTS, JournalForm, WeeklyReviewForm
from .models import JournalEntry, WeeklyReview

CURRENCIES = [c for c, _ in CURRENCY_CHOICES]


# ------------------------------------------------------------------ reviews
@login_required
def review_list(request):
    user = request.user
    today = local_today(user)
    current = week_start_for(today, user.profile.week_start)
    page = Paginator(WeeklyReview.objects.filter(owner=user), 12).get_page(request.GET.get("page"))
    done_now = WeeklyReview.objects.filter(owner=user, week_start=current).exists()
    return render(request, "reflections/review_list.html", {"page_title": "Reviews & Insights", "tab": "reviews", "page_obj": page, "current_week": current,
                                                            "current_done": done_now, "total_sections": len(PROMPTS), "qs": ""})


@login_required
def review_week(request, week):
    user = request.user
    today = local_today(user)
    day = parse_date(week)
    if day is None:
        return redirect("reflections:review_week", week=week_start_for(today, user.profile.week_start).isoformat())
    ws = week_start_for(day, user.profile.week_start)
    current = week_start_for(today, user.profile.week_start)
    if ws != day or ws > current:                       # normalise to the user's week start; no reviewing the future
        return redirect("reflections:review_week", week=min(ws, current).isoformat())
    existing = WeeklyReview.objects.filter(owner=user, week_start=ws).first()
    form = WeeklyReviewForm(request.POST or None, instance=existing, user=user, week_start=ws)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Review saved.")
        return redirect("reflections:review_week", week=ws.isoformat())
    currency = request.GET.get("currency") if request.GET.get("currency") in CURRENCIES else user.profile.currency
    snap = ins.week_snapshot(user, ws, today, currency)
    return render(request, "reflections/review_form.html", {
        "page_title": "Weekly review", "tab": "reviews", "form": form, "review": existing, "snap": snap, "ws": ws, "is_current": ws == current,
        "prev_week": (ws - dt.timedelta(days=7)).isoformat(), "next_week": (ws + dt.timedelta(days=7)).isoformat() if ws < current else None, "currency": currency,
        "prompts": list(form)})


@login_required
def review_delete(request, pk):
    r = get_object_or_404(WeeklyReview, pk=pk, owner=request.user)
    back = reverse("reflections:review_week", args=[r.week_start.isoformat()])
    return confirm_and_delete(request, r, "review", back, reverse("reflections:list"), lambda o: o.delete(), "Review deleted.")


# ------------------------------------------------------------------ journal
@login_required
def journal_list(request):
    user, g = request.user, request.GET
    qs = JournalEntry.objects.filter(owner=user).only("id", "date", "title", "body")
    if q := g.get("q", "").strip():
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    if d := parse_date(g.get("from")):
        qs = qs.filter(date__gte=d)
    if d := parse_date(g.get("to")):
        qs = qs.filter(date__lte=d)
    page = Paginator(qs, 20).get_page(g.get("page"))
    params = g.copy(); params.pop("page", None)
    return render(request, "reflections/journal_list.html", {"page_title": "Journal", "tab": "journal", "page_obj": page, "qs": params.urlencode(), "f": g,
                                                              "has_any": JournalEntry.objects.filter(owner=user).exists()})


@login_required
def journal_form(request, pk=None):
    entry = get_object_or_404(JournalEntry, pk=pk, owner=request.user) if pk else None
    form = JournalForm(request.POST or None, instance=entry, user=request.user, initial={"date": local_today(request.user)})
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, "Entry saved.")
        return redirect("reflections:journal_detail", pk=saved.pk)
    return render(request, "components/form_page.html", {"form": form, "page_title": "Edit entry" if entry else "New journal entry",
                                                         "cancel_url": reverse("reflections:journal_detail", args=[entry.pk]) if entry else reverse("reflections:journal"),
                                                         "extra": "Private to you. It is never shown on the dashboard."})


@login_required
def journal_detail(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, owner=request.user)
    return render(request, "reflections/journal_detail.html", {"page_title": entry.title or "Journal entry", "tab": "journal", "entry": entry})


@login_required
def journal_delete(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, owner=request.user)
    return confirm_and_delete(request, entry, "journal entry", reverse("reflections:journal_detail", args=[entry.pk]), reverse("reflections:journal"),
                              lambda o: o.delete(), "Entry deleted.")


# ----------------------------------------------------------------- insights
@login_required
def insights(request):
    user = request.user
    today = local_today(user)
    cur = request.GET.get("currency") if request.GET.get("currency") in CURRENCIES else user.profile.currency
    goals, tasks, habits = ins.goal_trend(user, today), ins.task_trend(user, today), ins.habit_trend(user, today)
    savings, spending = ins.savings_trend(user, cur, today), ins.spending_trend(user, cur, today)
    chart = {
        "goals": {"labels": [r["label"] for r in goals["rows"]], "goals": [r["goals"] for r in goals["rows"]], "milestones": [r["milestones"] for r in goals["rows"]]},
        "tasks": {"labels": [r["label"] for r in tasks["rows"]], "completed": [r["completed"] for r in tasks["rows"]]},
        "habits": {"labels": [r["label"] for r in habits["rows"]], "pct": [r["pct"] for r in habits["rows"]]},
        "savings": {"labels": [r["label"] for r in savings["rows"]], "contributed": [float(r["contributed"]) for r in savings["rows"]],
                    "cumulative": [float(r["cumulative"]) for r in savings["rows"]]},
        "spending": {"labels": [r["label"] for r in spending["rows"]], "income": [float(r["income"]) for r in spending["rows"]],
                     "spending": [float(r["spending"]) for r in spending["rows"]]},
    }
    has_data = any([goals["completed_total"], goals["active"], any(r["completed"] or r["due"] for r in tasks["rows"]), habits["per_habit"],
                    any(r["contributed"] for r in savings["rows"]), any(r["income"] or r["spending"] for r in spending["rows"])])
    return render(request, "reflections/insights.html", {"page_title": "Insights", "tab": "insights", "goals": goals, "tasks": tasks, "habits": habits,
                                                          "savings": savings, "spending": spending, "chart": chart, "currency": cur, "currencies": CURRENCIES, "has_data": has_data})
