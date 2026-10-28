from django.conf import settings
from django.urls import reverse

NAV_ITEMS = [
    ("dashboard:home", "Dashboard", "layout-dashboard"),
    ("goals:list", "My Goals", "target"),
    ("planner:today", "Planner", "calendar-check"),
    ("planner:habits", "Habits", "repeat"),
    ("finance:overview", "Finances", "wallet"),
    ("core:future_plans", "Future Plans", "compass"),
    ("core:interests", "Hobbies & Bucket List", "sparkles"),
    ("core:reviews", "Reviews & Insights", "line-chart"),
    ("accounts:settings", "Settings", "settings"),
]


def site(request):
    path = getattr(request, "path", "")
    nav = []
    for name, label, icon in NAV_ITEMS:
        url = reverse(name)
        # Dashboard is exact-match; other sections stay highlighted on their sub-pages.
        active = path == url if name == "dashboard:home" else path.startswith(url) or (
            name == "planner:today" and path.startswith("/planner/"))
        if name == "finance:overview":
            active = path.startswith("/finance/")
        nav.append({"url": url, "label": label, "icon": icon, "active": active})
    theme = "system"
    if getattr(request, "user", None) and request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
        if profile:
            theme = profile.theme
    return {"SITE_NAME": settings.SITE_NAME, "nav_items": nav, "theme_pref": theme}
