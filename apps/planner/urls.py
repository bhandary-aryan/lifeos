from django.urls import path

from . import views
from .views import TaskListView

app_name = "planner"

urlpatterns = [
    path("planner/", views.day_view, name="today"),
    path("planner/week/", views.week_view, name="week"),
    path("planner/upcoming/", TaskListView.as_view(view_name="upcoming"), name="upcoming"),
    path("planner/overdue/", TaskListView.as_view(view_name="overdue"), name="overdue"),
    path("planner/tasks/", TaskListView.as_view(view_name="all"), name="all"),
    path("planner/tasks/new/", views.TaskCreateView.as_view(), name="task_create"),
    path("planner/tasks/quick-add/", views.task_quick_add, name="quick_add"),
    path("planner/tasks/<int:pk>/edit/", views.TaskUpdateView.as_view(), name="task_update"),
    path("planner/tasks/<int:pk>/delete/", views.TaskDeleteView.as_view(), name="task_delete"),
    path("planner/tasks/<int:pk>/toggle/", views.task_toggle, name="task_toggle"),
    path("habits/", views.habit_tracker, name="habits"),
    path("habits/new/", views.HabitCreateView.as_view(), name="habit_create"),
    path("habits/<int:pk>/", views.HabitDetailView.as_view(), name="habit_detail"),
    path("habits/<int:pk>/edit/", views.HabitUpdateView.as_view(), name="habit_update"),
    path("habits/<int:pk>/delete/", views.HabitDeleteView.as_view(), name="habit_delete"),
    path("habits/<int:pk>/log/", views.habit_log_toggle, name="habit_log"),
    path("habits/<int:pk>/pause/", views.habit_pause_toggle, name="habit_pause"),
]
