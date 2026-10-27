from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.models import OwnedModel, Priority

DEFAULT_LIFE_AREAS = [
    "Career", "Education", "Financial Stability", "Health", "Relationships",
    "Home", "Personal Growth", "Creativity", "Travel",
]


class LifeArea(OwnedModel):
    name = models.CharField(max_length=60)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "owner", name="lifearea_owner_name_ci_unique"),
        ]

    def __str__(self):
        return self.name


class Goal(OwnedModel):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    class ProgressMode(models.TextChoices):
        MANUAL = "manual", "Manual percentage"
        MILESTONE = "milestone", "From milestones"
        TASK = "task", "From tasks"

    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    life_area = models.ForeignKey(LifeArea, null=True, blank=True, on_delete=models.SET_NULL, related_name="goals")
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    start_date = models.DateField(null=True, blank=True)
    target_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.NOT_STARTED)
    progress_mode = models.CharField(max_length=10, choices=ProgressMode.choices, default=ProgressMode.MILESTONE)
    manual_progress = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Used only when progress mode is Manual (0–100).",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["target_date", "title"]
        constraints = [
            models.CheckConstraint(condition=models.Q(manual_progress__gte=0, manual_progress__lte=100), name="goal_manual_progress_0_100"),
            models.CheckConstraint(
                condition=models.Q(start_date__isnull=True) | models.Q(target_date__isnull=True) | models.Q(target_date__gte=models.F("start_date")),
                name="goal_target_not_before_start",
            ),
        ]
        indexes = [models.Index(fields=["owner", "status"]), models.Index(fields=["owner", "target_date"])]

    def __str__(self):
        return self.title

    def clean(self):
        if self.life_area_id and self.life_area.owner_id != self.owner_id:
            raise ValidationError({"life_area": "Choose one of your own life areas."})
        if self.start_date and self.target_date and self.target_date < self.start_date:
            raise ValidationError({"target_date": "Target date cannot be before the start date."})

    def save(self, *args, **kwargs):
        if self.status == self.Status.COMPLETED:
            if not self.completed_at:
                self.completed_at = timezone.now()
        else:
            self.completed_at = None
        super().save(*args, **kwargs)


class Milestone(OwnedModel):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name="milestones")
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.TODO)
    order = models.PositiveIntegerField(default=0)
    weight = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "due_date", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(weight__gte=1), name="milestone_weight_positive")]
        indexes = [models.Index(fields=["goal", "status"])]

    def __str__(self):
        return self.title

    def clean(self):
        if self.goal_id and self.goal.owner_id != self.owner_id:
            raise ValidationError("A milestone must belong to one of your own goals.")

    def save(self, *args, **kwargs):
        if self.status == self.Status.COMPLETED:
            if not self.completed_at:
                self.completed_at = timezone.now()
        else:
            self.completed_at = None
        super().save(*args, **kwargs)
