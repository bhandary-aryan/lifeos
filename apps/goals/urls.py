from django.urls import path

from . import views

app_name = "goals"

urlpatterns = [
    path("goals/", views.GoalListView.as_view(), name="list"),
    path("goals/new/", views.GoalCreateView.as_view(), name="create"),
    path("goals/areas/", views.life_areas, name="areas"),
    path("goals/areas/<int:pk>/delete/", views.life_area_delete, name="area_delete"),
    path("goals/<int:pk>/", views.GoalDetailView.as_view(), name="detail"),
    path("goals/<int:pk>/edit/", views.GoalUpdateView.as_view(), name="update"),
    path("goals/<int:pk>/delete/", views.GoalDeleteView.as_view(), name="delete"),
    path("goals/<int:pk>/milestones/add/", views.milestone_add, name="milestone_add"),
    path("goals/milestones/<int:pk>/edit/", views.MilestoneUpdateView.as_view(), name="milestone_update"),
    path("goals/milestones/<int:pk>/delete/", views.MilestoneDeleteView.as_view(), name="milestone_delete"),
    path("goals/milestones/<int:pk>/toggle/", views.milestone_toggle, name="milestone_toggle"),
]
