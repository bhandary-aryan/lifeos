from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.goals.urls")),
    path("", include("apps.planner.urls")),
    path("", include("apps.finance.urls")),
    path("", include("apps.future_plans.urls")),
    path("", include("apps.interests.urls")),
    path("", include("apps.core.urls")),
    path("", include("apps.dashboard.urls")),
]
