from django.urls import path

from . import views as v

app_name = "future_plans"

urlpatterns = [
    path("future-plans/", v.plan_list, name="list"),
    path("future-plans/new/", v.plan_form, name="create"),
    path("future-plans/affordability/", v.affordability_view, name="affordability"),
    path("future-plans/<int:pk>/", v.plan_detail, name="detail"),
    path("future-plans/<int:pk>/edit/", v.plan_form, name="update"),
    path("future-plans/<int:pk>/delete/", v.plan_delete, name="delete"),
    path("future-plans/<int:pk>/affordability/", v.affordability_view, name="plan_affordability"),
    path("future-plans/<int:pk>/create-savings-goal/", v.plan_create_savings, name="create_savings"),
    path("future-plans/<int:pk>/actions/add/", v.action_add, name="action_add"),
    path("future-plans/actions/<int:pk>/toggle/", v.action_toggle, name="action_toggle"),
    path("future-plans/actions/<int:pk>/delete/", v.action_delete, name="action_delete"),
]
