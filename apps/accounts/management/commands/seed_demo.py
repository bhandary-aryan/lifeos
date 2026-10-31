"""Create (or recreate) a clearly-labelled DEMO account with sample data, for development only.

    python manage.py seed_demo            # prints the demo login

Safety rules (spec section 19: never populate real accounts with fabricated figures)
- Refuses to run unless DEBUG is on (so never under config.settings.prod).
- Touches only the single demo user (email ends with DEMO_EMAIL_SUFFIX); it never edits other accounts.
- Every title/name it creates contains "[Demo]" and the app shows a banner while signed in as the demo user.
"""
import datetime as dt
import secrets
from decimal import Decimal as D

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

DEMO_EMAIL_SUFFIX = "@demo.lifeos.invalid"
DEMO_EMAIL = "demo" + DEMO_EMAIL_SUFFIX
TAG = "[Demo] "


class Command(BaseCommand):
    help = "Create a labelled demo account with sample data (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Password for the demo account (random if omitted).")

    @transaction.atomic
    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with DEBUG enabled. It will never populate a production database.")
        from django.contrib.auth import get_user_model

        from apps.core.services import local_today
        from apps.finance import services as fin
        from apps.finance.models import Account, Budget, Category, Debt, SavingsContribution, SavingsGoal, Transaction
        from apps.future_plans.models import FuturePlan, PlanAction
        from apps.goals.models import Goal, LifeArea, Milestone
        from apps.interests.models import InterestItem
        from apps.planner.models import Habit, HabitLog, Task
        from apps.reflections.models import JournalEntry, WeeklyReview

        User = get_user_model()
        User.objects.filter(email=DEMO_EMAIL).delete()                 # idempotent: rebuild from scratch
        password = opts["password"] or secrets.token_urlsafe(12)
        user = User.objects.create_user(DEMO_EMAIL, password)
        p = user.profile
        p.display_name, p.onboarding_completed, p.currency = "Demo (sample data)", True, "GBP"
        p.save()
        today = local_today(user)
        d = lambda n: today + dt.timedelta(days=n)  # noqa: E731
        T = Transaction.Type

        goal = Goal.objects.create(owner=user, title=TAG + "Learn Spanish", life_area=LifeArea.objects.get(owner=user, name="Education"), priority="high",
                                   status="in_progress", progress_mode="milestone", start_date=d(-60), target_date=d(120))
        m1 = Milestone.objects.create(owner=user, goal=goal, title=TAG + "Finish beginner course", status="completed", weight=2, due_date=d(-10), order=0)
        m2 = Milestone.objects.create(owner=user, goal=goal, title=TAG + "Hold a 10-minute conversation", weight=3, due_date=d(60), order=1)
        g2 = Goal.objects.create(owner=user, title=TAG + "Run a 10k", priority="medium", status="in_progress", progress_mode="task", target_date=d(90))
        Goal.objects.create(owner=user, title=TAG + "Read 12 books", progress_mode="manual", manual_progress=25, status="in_progress")
        Task.objects.create(owner=user, title=TAG + "Vocabulary practice", goal=goal, milestone=m2, due_date=today, estimated_minutes=30, priority="high")
        Task.objects.create(owner=user, title=TAG + "Book a tutor", goal=goal, due_date=d(2), estimated_minutes=15)
        Task.objects.create(owner=user, title=TAG + "Buy running shoes", goal=g2, due_date=d(-2), status="todo")
        done = Task.objects.create(owner=user, title=TAG + "Plan a training schedule", goal=g2, due_date=d(-5), status="completed")
        Task.objects.create(owner=user, title=TAG + "Weekly shop", due_date=today, estimated_minutes=60)
        del m1, done

        run = Habit.objects.create(owner=user, name=TAG + "Morning run", frequency="weekdays", weekdays=[0, 2, 4], start_date=d(-40))
        for n in range(1, 40):
            day = d(-n)
            if day.weekday() in (0, 2, 4) and n % 5:
                HabitLog.objects.get_or_create(owner=user, habit=run, date=day)
        read = Habit.objects.create(owner=user, name=TAG + "Read 20 minutes", frequency="daily", start_date=d(-30))
        for n in range(1, 30, 2):
            HabitLog.objects.get_or_create(owner=user, habit=read, date=d(-n))

        cur = Account.objects.create(owner=user, name=TAG + "Current account", opening_balance=D("1500.00"))
        sav = Account.objects.create(owner=user, name=TAG + "Savings account", account_type="savings")
        cat = lambda n, k="expense": Category.objects.get(owner=user, name=n, kind=k)  # noqa: E731
        debt = Debt.objects.create(owner=user, creditor=TAG + "Student loan", original_amount=D("4000.00"), interest_rate=D("4.50"),
                                   minimum_payment=D("120.00"), due_day=min(28, d(5).day), start_date=d(-300))

        def tx(kind, amount, day, **kw):
            fin.save_transaction(Transaction(owner=user, type=kind, amount=D(amount), date=day, account=kw.pop("account", cur), **kw))

        for m in range(0, 3):
            first = (today.replace(day=1) - dt.timedelta(days=30 * m)).replace(day=1)
            tx(T.INCOME, "2400.00", first.replace(day=1), category=cat("Salary", "income"), description=TAG + "Pay")
            tx(T.EXPENSE, "850.00", first.replace(day=2), category=cat("Rent / Mortgage"), description=TAG + "Rent")
            tx(T.EXPENSE, str(D("260.00") + 15 * m), first.replace(day=5), category=cat("Groceries"), description=TAG + "Food shop")
            tx(T.EXPENSE, "64.50", first.replace(day=9), category=cat("Transport"), description=TAG + "Travel card")
            tx(T.DEBT_PAYMENT, "120.00", first.replace(day=10), debt=debt, principal_amount=D("105.00"), interest_amount=D("15.00"))
        Budget.objects.create(owner=user, category=cat("Groceries"), year=today.year, month=today.month, currency="GBP", limit_amount=D("300.00"))
        Budget.objects.create(owner=user, category=cat("Transport"), year=today.year, month=today.month, currency="GBP", limit_amount=D("60.00"))

        trip = SavingsGoal.objects.create(owner=user, name=TAG + "Holiday fund", target_amount=D("1800.00"), target_date=d(240), priority="medium")
        fin.save_contribution(SavingsContribution(owner=user, goal=trip, date=d(-20), amount=D("200.00")), cur, sav)
        fin.save_contribution(SavingsContribution(owner=user, goal=trip, date=d(-3), amount=D("150.00")))
        plan = FuturePlan.objects.create(owner=user, title=TAG + "Trip to Japan", category="travel", estimated_cost=D("2400.00"), target_date=d(240),
                                         savings_goal=trip, status="planning")
        PlanAction.objects.create(owner=user, plan=plan, title=TAG + "Check passport validity", due_date=d(10))

        InterestItem.objects.create(owner=user, name=TAG + "Pottery class", category="activity", status="want", tags=["creative"])
        InterestItem.objects.create(owner=user, name=TAG + "See the northern lights", category="travel", is_bucket_list=True, target_date=d(45), tags=["travel"])
        InterestItem.objects.create(owner=user, name=TAG + "Chess", category="game", status="enjoy")

        ws = today - dt.timedelta(days=today.weekday() + 7)
        WeeklyReview.objects.create(owner=user, week_start=ws, accomplishments=TAG + "Sample review text.", next_priorities=TAG + "Sample priority.")
        JournalEntry.objects.create(owner=user, date=d(-1), title=TAG + "Sample entry", body=TAG + "This is sample journal text.")

        self.stdout.write(self.style.SUCCESS("Demo account ready. Everything in it is sample data labelled [Demo]."))
        self.stdout.write(f"  email:    {DEMO_EMAIL}\n  password: {password}")
