"""Account-level operations kept out of views."""
from django.db import transaction

from .models import Profile


def export_user_data(user):
    """Everything we hold about the user, as plain JSON-serialisable data.

    Later phases append their own sections here.
    """
    p = user.profile
    return {
        "account": {
            "email": user.email,
            "date_joined": user.date_joined.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
        },
        "profile": {
            "display_name": p.display_name,
            "currency": p.currency,
            "timezone": p.timezone,
            "date_format": p.date_format,
            "week_start": p.week_start,
            "theme": p.theme,
            "life_areas": p.life_areas,
            "onboarding_completed": p.onboarding_completed,
        },
    }


@transaction.atomic
def delete_account(user):
    """Permanently delete the user. Owned records cascade via foreign keys."""
    user.delete()


def ensure_profile(user) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile
