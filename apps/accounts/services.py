"""Account-level operations kept out of views."""
from django.db import transaction

from .models import Profile


def export_user_data(user):
    """Everything we hold about the user, as plain JSON-serialisable data.

    Later phases append their own sections here.
    """
    from apps.goals.models import Goal, LifeArea, Milestone
    from apps.planner.models import Habit, HabitLog, Task

    p = user.profile
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
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
            "daily_available_minutes": p.daily_available_minutes,
            "theme": p.theme,
            "life_areas": p.life_areas,
            "onboarding_completed": p.onboarding_completed,
        },
        "life_areas": list(LifeArea.objects.filter(owner=user).values_list("name", flat=True)),
        "goals": [
            {
                "id": g.pk, "title": g.title, "description": g.description,
                "life_area": g.life_area.name if g.life_area else None, "priority": g.priority,
                "status": g.status, "progress_mode": g.progress_mode, "manual_progress": g.manual_progress,
                "start_date": iso(g.start_date), "target_date": iso(g.target_date),
                "completed_at": iso(g.completed_at), "notes": g.notes,
            }
            for g in Goal.objects.filter(owner=user).select_related("life_area")
        ],
        "milestones": [
            {
                "id": m.pk, "goal_id": m.goal_id, "title": m.title, "description": m.description,
                "due_date": iso(m.due_date), "status": m.status, "order": m.order, "weight": m.weight,
                "completed_at": iso(m.completed_at),
            }
            for m in Milestone.objects.filter(owner=user)
        ],
        "tasks": [
            {
                "id": t.pk, "title": t.title, "description": t.description, "due_date": iso(t.due_date),
                "start_at": iso(t.start_at), "estimated_minutes": t.estimated_minutes, "priority": t.priority,
                "status": t.status, "goal_id": t.goal_id, "milestone_id": t.milestone_id, "weight": t.weight,
                "completed_at": iso(t.completed_at),
            }
            for t in Task.objects.filter(owner=user)
        ],
        "habits": [
            {
                "id": h.pk, "name": h.name, "description": h.description, "frequency": h.frequency,
                "weekdays": h.weekdays, "weekly_target": h.weekly_target, "start_date": iso(h.start_date),
                "end_date": iso(h.end_date), "is_active": h.is_active, "paused_on": iso(h.paused_on),
            }
            for h in Habit.objects.filter(owner=user)
        ],
        "habit_logs": [
            {"habit_id": l.habit_id, "date": iso(l.date), "note": l.note}
            for l in HabitLog.objects.filter(owner=user)
        ],
    }


@transaction.atomic
def delete_account(user):
    """Permanently delete the user. Owned records cascade via foreign keys."""
    user.delete()


def ensure_profile(user) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile
