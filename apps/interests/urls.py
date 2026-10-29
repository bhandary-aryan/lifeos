from django.urls import path

from . import views as v

app_name = "interests"

urlpatterns = [
    path("interests/", v.item_list, name="list"),
    path("interests/new/", v.item_form, name="create"),
    path("interests/<int:pk>/", v.item_detail, name="detail"),
    path("interests/<int:pk>/edit/", v.item_form, name="update"),
    path("interests/<int:pk>/delete/", v.item_delete, name="delete"),
    path("interests/<int:pk>/status/", v.item_status, name="status"),
    path("interests/<int:pk>/add-to-planner/", v.item_to_task, name="to_task"),
    path("interests/<int:pk>/image/", v.item_image, name="image"),
]
