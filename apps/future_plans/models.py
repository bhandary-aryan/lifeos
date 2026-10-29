"""Future life plans.

Three money figures are kept deliberately SEPARATE and never merged:
  estimated cost  - what the user thinks the plan will cost (FuturePlan.estimated_cost)
  savings target  - what the linked SavingsGoal aims to save
  actual savings  - the sum of the linked goal's real contributions (derived)
Projections (affordability page) are a fourth thing, computed on demand and never stored.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import CURRENCY_CHOICES
from apps.core.models import OwnedModel, Priority
from apps.finance.models import MONEY, SavingsGoal
from apps.goals.models import Goal


class FuturePlan(OwnedModel):
    class Category(models.TextChoices):
        HOUSE = "house", "Buy or build a house"
        WEDDING = "wedding", "Wedding"
        VEHICLE = "vehicle", "Vehicle"
        RELOCATION = "relocation", "Move or relocate"
        EDUCATION = "education", "Further education"
        BUSINESS = "business", "Start a business"
        TRAVEL = "travel", "Travel"
        EMERGENCY = "emergency", "Emergency fund"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        IDEA = "idea", "Idea"
        PLANNING = "planning", "Planning"
        IN_PROGRESS = "in_progress", "In progress"
        ON_HOLD = "on_hold", "On hold"
        ACHIEVED = "achieved", "Achieved"
        CANCELLED = "cancelled", "Cancelled"

    title = models.CharField(max_length=160)
    category = models.CharField(max_length=15, choices=Category.choices, default=Category.OTHER)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PLANNING)
    target_date = models.DateField(null=True, blank=True)
    estimated_cost = models.DecimalField(null=True, blank=True, **MONEY)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="GBP")
    goal = models.ForeignKey(Goal, null=True, blank=True, on_delete=models.SET_NULL, related_name="future_plans")
    savings_goal = models.OneToOneField(SavingsGoal, null=True, blank=True, on_delete=models.SET_NULL, related_name="future_plan")
    achieved_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["target_date", "title"]
        constraints = [models.CheckConstraint(condition=Q(estimated_cost__isnull=True) | Q(estimated_cost__gt=0), name="plan_cost_positive")]
        indexes = [models.Index(fields=["owner", "status"]), models.Index(fields=["owner", "target_date"])]

    def __str__(self):
        return self.title

    @property
    def is_open(self):
        return self.status not in (self.Status.ACHIEVED, self.Status.CANCELLED)

    def clean(self):
        errors = {}
        if self.goal_id and self.goal.owner_id != self.owner_id:
            errors["goal"] = "Choose one of your own goals."
        if self.savings_goal_id:
            sg = self.savings_goal
            if sg.owner_id != self.owner_id:
                errors["savings_goal"] = "Choose one of your own savings goals."
            elif sg.currency != self.currency:
                errors["savings_goal"] = f"The savings goal is in {sg.currency} but this plan is in {self.currency}. Amounts are never converted."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.status == self.Status.ACHIEVED:
            if not self.achieved_on:
                self.achieved_on = timezone.localdate()
        else:
            self.achieved_on = None
        super().save(*args, **kwargs)


class PlanAction(OwnedModel):
    plan = models.ForeignKey(FuturePlan, on_delete=models.CASCADE, related_name="actions")
    title = models.CharField(max_length=200)
    notes = models.CharField(max_length=300, blank=True)
    due_date = models.DateField(null=True, blank=True)
    is_done = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["is_done", "order", "due_date", "id"]

    def __str__(self):
        return self.title

    def clean(self):
        if self.plan_id and self.plan.owner_id != self.owner_id:
            raise ValidationError("An action must belong to one of your own plans.")

    def save(self, *args, **kwargs):
        if self.is_done and not self.completed_at:
            self.completed_at = timezone.now()
        elif not self.is_done:
            self.completed_at = None
        super().save(*args, **kwargs)
