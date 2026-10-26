"""Dashboard summary calculations.

Phase 1 has no goal/task/finance data yet, so the summary carries only
greeting/date context and a card per upcoming module (with an honest empty
message, never a fabricated figure). Later phases replace each card's
`empty` text with real aggregates computed here, in one place, rather than
inside templates.
"""
from django.urls import reverse

from apps.accounts.models import LIFE_AREA_CHOICES
from apps.core.services import local_now


def greeting_for(hour: int) -> str:
    if hour < 5:
        return "Good evening"
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


MODULE_CARDS = [
    ("Today's priorities", "core:planner", "calendar-check", "Task planning is coming in Phase 2.", "Phase 2"),
    ("Active goals", "core:goals", "target", "Goal tracking is coming in Phase 2.", "Phase 2"),
    ("Cash flow this month", "core:finances", "wallet", "Finance tracking is coming in Phase 3.", "Phase 3"),
    ("Habits this week", "core:habits", "repeat", "Habit tracking is coming in Phase 2.", "Phase 2"),
]


def build_dashboard_summary(user):
    now = local_now(user)
    profile = user.profile
    area_names = dict(LIFE_AREA_CHOICES)
    return {
        "greeting": greeting_for(now.hour),
        "name": user.get_short_name(),
        "today": now.date(),
        "onboarding_pending": not profile.onboarding_completed,
        "life_areas": profile.life_areas,
        "life_area_labels": [area_names[a] for a in profile.life_areas if a in area_names],
        "cards": [
            {"label": label, "url": reverse(url), "icon": icon, "empty": empty, "phase": phase}
            for label, url, icon, empty, phase in MODULE_CARDS
        ],
    }
