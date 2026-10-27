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
