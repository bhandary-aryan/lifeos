from .models import REVIEW_FIELDS, WeeklyReview


def review_for_week(user, week_start):
    return WeeklyReview.objects.filter(owner=user, week_start=week_start).first()


def reviewed_week_count(user):
    return WeeklyReview.objects.filter(owner=user).count()
