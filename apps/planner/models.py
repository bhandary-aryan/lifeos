from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import OwnedModel, Priority
from apps.goals.models import Goal, LifeArea, Milestone


class Task(OwnedModel):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    start_at = models.DateTimeField(null=True, blank=True, help_text="Optional time block start.")
    estimated_minutes = models.PositiveIntegerField(null=True, blank=True, validators=[MaxValueValidator(1440)])
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.TODO)
    goal = models.ForeignKey(Goal, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    milestone = models.ForeignKey(Milestone, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    weight = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(weight__gte=1), name="task_weight_positive")]
        indexes = [
            models.Index(fields=["owner", "status", "due_date"]),
            models.Index(fields=["owner", "due_date"]),
            models.Index(fields=["goal", "status"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_done(self):
        return self.status == self.Status.COMPLETED

    def clean(self):
        errors = {}
        if self.goal_id and self.goal.owner_id != self.owner_id:
            errors["goal"] = "Choose one of your own goals."
        if self.milestone_id:
            if self.milestone.owner_id != self.owner_id:
                errors["milestone"] = "Choose one of your own milestones."
            elif self.goal_id and self.milestone.goal_id != self.goal_id:
                errors["milestone"] = "That milestone belongs to a different goal."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # A task linked to a milestone always belongs to that milestone's goal.
        if self.milestone_id and not self.goal_id:
            self.goal_id = self.milestone.goal_id
        if self.status == self.Status.COMPLETED:
            if not self.completed_at:
                self.completed_at = timezone.now()
        else:
            self.completed_at = None
        super().save(*args, **kwargs)


class Habit(OwnedModel):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Every day"
        WEEKDAYS = "weekdays", "Selected weekdays"
        WEEKLY = "weekly", "Times per week"

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.DAILY)
    weekdays = models.JSONField(default=list, blank=True, help_text="0=Monday … 6=Sunday (used for selected weekdays).")
    weekly_target = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(7)])
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    paused_on = models.DateField(null=True, blank=True)
    category = models.ForeignKey(LifeArea, null=True, blank=True, on_delete=models.SET_NULL, related_name="habits")

    class Meta:
        ordering = ["-is_active", "name"]
        constraints = [
            models.CheckConstraint(condition=models.Q(weekly_target__gte=1, weekly_target__lte=7), name="habit_weekly_target_1_7"),
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True) | models.Q(end_date__gte=models.F("start_date")),
                name="habit_end_not_before_start",
            ),
        ]
        indexes = [models.Index(fields=["owner", "is_active"])]

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        if self.category_id and self.category.owner_id != self.owner_id:
            errors["category"] = "Choose one of your own life areas."
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "End date cannot be before the start date."
        if self.frequency == self.Frequency.WEEKDAYS:
            days = self.weekdays or []
            if not days or any((not isinstance(d, int)) or d < 0 or d > 6 for d in days):
                errors["weekdays"] = "Pick at least one weekday."
        if errors:
            raise ValidationError(errors)


class HabitLog(OwnedModel):
    habit = models.ForeignKey(Habit, on_delete=models.CASCADE, related_name="logs")
    date = models.DateField(help_text="The user's local calendar date of the completion.")
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["habit", "date"], name="habitlog_one_per_habit_per_date")]
        indexes = [models.Index(fields=["owner", "date"])]

    def clean(self):
        if self.habit_id and self.habit.owner_id != self.owner_id:
            raise ValidationError("A habit log must belong to one of your own habits.")
