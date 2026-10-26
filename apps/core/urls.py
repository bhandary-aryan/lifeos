from django.urls import path

from .views import ComingSoonView

app_name = "core"

urlpatterns = [
    path("goals/", ComingSoonView.as_view(
        title="My Goals", phase="Phase 2", icon="target",
        description="Goals, milestones and progress tracking."), name="goals"),
    path("planner/", ComingSoonView.as_view(
        title="Planner", phase="Phase 2", icon="calendar-check",
        description="Tasks, schedule and weekly planning."), name="planner"),
    path("habits/", ComingSoonView.as_view(
        title="Habits", phase="Phase 2", icon="repeat",
        description="Habit tracking with streaks and consistency stats."), name="habits"),
    path("finances/", ComingSoonView.as_view(
        title="Finances", phase="Phase 3", icon="wallet",
        description="Accounts, transactions, budgets, debts and savings goals."), name="finances"),
    path("future-plans/", ComingSoonView.as_view(
        title="Future Plans", phase="Phase 4", icon="compass",
        description="Major life milestones and target-date projections."), name="future_plans"),
    path("interests/", ComingSoonView.as_view(
        title="Hobbies & Bucket List", phase="Phase 4", icon="sparkles",
        description="Hobbies, favourites and your bucket list."), name="interests"),
    path("reviews/", ComingSoonView.as_view(
        title="Reviews & Insights", phase="Phase 5", icon="line-chart",
        description="Weekly reviews, journal and historical insights."), name="reviews"),
]
