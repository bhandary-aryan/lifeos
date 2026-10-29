from django.urls import path

from .views import ComingSoonView

app_name = "core"

urlpatterns = [
    path("reviews/", ComingSoonView.as_view(
        title="Reviews & Insights", phase="Phase 5", icon="line-chart",
        description="Weekly reviews, journal and historical insights."), name="reviews"),
]
