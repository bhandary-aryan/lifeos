from django.conf import settings
from django.urls import reverse

NAV_ITEMS = [
    ("dashboard:home", "Dashboard", "layout-dashboard"),
    ("core:goals", "My Goals", "target"),
    ("core:planner", "Planner", "calendar-check"),
    ("core:habits", "Habits", "repeat"),
    ("core:finances", "Finances", "wallet"),
    ("core:future_plans", "Future Plans", "compass"),
    ("core:interests", "Hobbies & Bucket List", "sparkles"),
    ("core:reviews", "Reviews & Insights", "line-chart"),
    ("accounts:settings", "Settings", "settings"),
]


def site(request):
    current = getattr(getattr(request, "resolver_match", None), "view_name", "")
    nav = [
        {"url": reverse(name), "label": label, "icon": icon, "active": name == current}
        for name, label, icon in NAV_ITEMS
    ]
    theme = "system"
    if getattr(request, "user", None) and request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
        if profile:
            theme = profile.theme
    return {"SITE_NAME": settings.SITE_NAME, "nav_items": nav, "theme_pref": theme}
