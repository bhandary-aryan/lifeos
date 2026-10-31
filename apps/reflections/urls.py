from django.urls import path

from . import views as v

app_name = "reflections"

urlpatterns = [
    path("reviews/", v.review_list, name="list"),
    path("reviews/week/<str:week>/", v.review_week, name="review_week"),
    path("reviews/<int:pk>/delete/", v.review_delete, name="review_delete"),
    path("journal/", v.journal_list, name="journal"),
    path("journal/new/", v.journal_form, name="journal_create"),
    path("journal/<int:pk>/", v.journal_detail, name="journal_detail"),
    path("journal/<int:pk>/edit/", v.journal_form, name="journal_update"),
    path("journal/<int:pk>/delete/", v.journal_delete, name="journal_delete"),
    path("insights/", v.insights, name="insights"),
]
