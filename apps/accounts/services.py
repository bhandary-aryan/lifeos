"""Account-level operations kept out of views."""
from django.db import transaction

from .models import Profile


def export_user_data(user):
    """Everything we hold about the user, as plain JSON-serialisable data.

    Later phases append their own sections here.
    """
    from apps.goals.models import Goal, LifeArea, Milestone
    from apps.planner.models import Habit, HabitLog, Task

    from apps.finance.models import (
        Account, Budget, Category, Debt, DebtDrawdown, SavingsContribution, SavingsGoal, Transaction)

    from apps.future_plans.models import FuturePlan, PlanAction
    from apps.interests.models import InterestItem

    from apps.reflections.models import JournalEntry, WeeklyReview

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
            "reminders_enabled": p.reminders_enabled,
            "reminder_lead_days": p.reminder_lead_days,
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
        "finance": {
            "accounts": [{"id": a.pk, "name": a.name, "type": a.account_type, "currency": a.currency,
                          "opening_balance": str(a.opening_balance), "is_active": a.is_active}
                         for a in Account.objects.filter(owner=user)],
            "categories": [{"id": c.pk, "name": c.name, "kind": c.kind} for c in Category.objects.filter(owner=user)],
            "transactions": [{"id": t.pk, "type": t.type, "date": iso(t.date), "amount": str(t.amount), "account_id": t.account_id,
                              "destination_account_id": t.destination_account_id, "category_id": t.category_id,
                              "description": t.description, "debt_id": t.debt_id, "principal": str(t.principal_amount),
                              "interest": str(t.interest_amount)} for t in Transaction.objects.filter(owner=user)],
            "debts": [{"id": d.pk, "creditor": d.creditor, "description": d.description, "currency": d.currency,
                       "original_amount": str(d.original_amount), "interest_rate": str(d.interest_rate),
                       "minimum_payment": str(d.minimum_payment), "due_day": d.due_day, "start_date": iso(d.start_date),
                       "expected_payoff_date": iso(d.expected_payoff_date), "status": d.status, "notes": d.notes}
                      for d in Debt.objects.filter(owner=user)],
            "debt_drawdowns": [{"debt_id": x.debt_id, "date": iso(x.date), "amount": str(x.amount), "note": x.note}
                               for x in DebtDrawdown.objects.filter(owner=user)],
            "budgets": [{"category_id": b.category_id, "year": b.year, "month": b.month, "currency": b.currency,
                         "limit_amount": str(b.limit_amount)} for b in Budget.objects.filter(owner=user)],
            "savings_goals": [{"id": g.pk, "name": g.name, "currency": g.currency, "target_amount": str(g.target_amount),
                               "target_date": iso(g.target_date), "priority": g.priority, "status": g.status, "notes": g.notes}
                              for g in SavingsGoal.objects.filter(owner=user)],
            "savings_contributions": [{"goal_id": c.goal_id, "date": iso(c.date), "amount": str(c.amount), "note": c.note,
                                       "transfer_id": c.transfer_id} for c in SavingsContribution.objects.filter(owner=user)],
        },
        "future_plans": [{"id": f.pk, "title": f.title, "category": f.category, "description": f.description, "priority": f.priority,
                          "status": f.status, "target_date": iso(f.target_date), "estimated_cost": str(f.estimated_cost) if f.estimated_cost is not None else None,
                          "currency": f.currency, "goal_id": f.goal_id, "savings_goal_id": f.savings_goal_id, "achieved_on": iso(f.achieved_on)}
                         for f in FuturePlan.objects.filter(owner=user)],
        "plan_actions": [{"plan_id": a.plan_id, "title": a.title, "notes": a.notes, "due_date": iso(a.due_date), "is_done": a.is_done}
                         for a in PlanAction.objects.filter(owner=user)],
        "interests": [{"id": i.pk, "name": i.name, "category": i.category, "status": i.status, "priority": i.priority, "description": i.description,
                       "notes": i.notes, "tags": i.tags, "has_image": bool(i.image), "is_bucket_list": i.is_bucket_list, "target_date": iso(i.target_date),
                       "estimated_cost": str(i.estimated_cost) if i.estimated_cost is not None else None, "currency": i.currency,
                       "linked_task_id": i.linked_task_id, "linked_plan_id": i.linked_plan_id, "completed_on": iso(i.completed_on)}
                      for i in InterestItem.objects.filter(owner=user)],
        "weekly_reviews": [{"week_start": iso(r.week_start), "accomplishments": r.accomplishments, "goals_progressed": r.goals_progressed,
                            "obstacles": r.obstacles, "lessons": r.lessons, "habits_went_well": r.habits_went_well,
                            "financial_observations": r.financial_observations, "postponed": r.postponed, "next_priorities": r.next_priorities}
                           for r in WeeklyReview.objects.filter(owner=user)],
        "journal_entries": [{"date": iso(j.date), "title": j.title, "body": j.body} for j in JournalEntry.objects.filter(owner=user)],
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
