from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .services import build_dashboard_summary


@login_required
def home(request):
    return render(
        request,
        "dashboard/home.html",
        {"page_title": "Dashboard", "summary": build_dashboard_summary(request.user), "next_url": request.get_full_path()},
    )


from django.http import HttpResponse  # noqa: E402

from apps.core.services import local_today  # noqa: E402

from .reminders import build_reminders  # noqa: E402


@login_required
def reminders(request):
    today = local_today(request.user)
    items = build_reminders(request.user, today)
    return render(request, "dashboard/reminders.html", {"page_title": "Reminders", "items": items, "today": today,
                                                        "enabled": request.user.profile.reminders_enabled, "lead": request.user.profile.reminder_lead_days})


@login_required
def reminders_badge(request):
    """Tiny HTML fragment for the top-bar bell; loaded lazily so ordinary page loads stay cheap."""
    n = len(build_reminders(request.user, local_today(request.user)))
    html = (f'<span class="badge badge-error badge-sm absolute -right-1 -top-1" aria-hidden="true">{n if n < 100 else "99+"}</span>'
            f'<span class="sr-only">{n} reminder{"s" if n != 1 else ""}</span>') if n else '<span class="sr-only">No reminders</span>'
    return HttpResponse(html)
