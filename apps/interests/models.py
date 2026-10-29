import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.accounts.models import CURRENCY_CHOICES
from apps.core.models import OwnedModel, Priority
from apps.finance.models import MONEY
from apps.future_plans.models import FuturePlan
from apps.planner.models import Task


def image_path(instance, filename):
    """Random file name: never trust the uploaded name, and never reveal it."""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "img").lower()
    ext = ext if ext in {"jpg", "jpeg", "png", "gif", "webp"} else "img"
    return f"interests/{instance.owner_id}/{uuid.uuid4().hex}.{ext}"


class InterestItem(OwnedModel):
    class Category(models.TextChoices):
        HOBBY = "hobby", "Hobby"
        SKILL = "skill", "Skill to learn"
        BOOK = "book", "Book"
        FILM = "film", "Film or show"
        MUSIC = "music", "Music"
        GAME = "game", "Game"
        PLACE = "place", "Place"
        ACTIVITY = "activity", "Activity to try"
        WEEKEND = "weekend", "Weekend idea"
        TRAVEL = "travel", "Travel experience"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        ENJOY = "enjoy", "I already enjoy this"
        WANT = "want", "Want to try"
        TRYING = "trying", "Trying it now"
        DONE = "done", "Done / tried"
        NOT_FOR_ME = "not_for_me", "Not for me"

    name = models.CharField(max_length=160)
    category = models.CharField(max_length=15, choices=Category.choices, default=Category.HOBBY)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to=image_path, null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.WANT)
    notes = models.TextField(blank=True)
    completed_on = models.DateField(null=True, blank=True)
    # Bucket-list extras (only valid when is_bucket_list)
    is_bucket_list = models.BooleanField(default=False)
    target_date = models.DateField(null=True, blank=True)
    estimated_cost = models.DecimalField(null=True, blank=True, **MONEY)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="GBP")
    linked_task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="interest_items")
    linked_plan = models.ForeignKey(FuturePlan, null=True, blank=True, on_delete=models.SET_NULL, related_name="interest_items")

    class Meta:
        ordering = ["name"]
        constraints = [models.CheckConstraint(condition=Q(estimated_cost__isnull=True) | Q(estimated_cost__gte=0), name="interest_cost_nonneg")]
        indexes = [models.Index(fields=["owner", "category"]), models.Index(fields=["owner", "status"]), models.Index(fields=["owner", "is_bucket_list"])]

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        if self.linked_task_id and self.linked_task.owner_id != self.owner_id:
            errors["linked_task"] = "Choose one of your own tasks."
        if self.linked_plan_id and self.linked_plan.owner_id != self.owner_id:
            errors["linked_plan"] = "Choose one of your own plans."
        if not self.is_bucket_list and (self.target_date or self.estimated_cost is not None or self.linked_task_id or self.linked_plan_id):
            errors["is_bucket_list"] = "Target date, cost and links apply to bucket-list items. Tick 'On my bucket list' or clear them."
        if not isinstance(self.tags, list) or any(not isinstance(t, str) for t in self.tags):
            errors["tags"] = "Tags must be a list of words."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from django.utils import timezone

        if self.status == self.Status.DONE:
            if not self.completed_on:
                self.completed_on = timezone.localdate()
        else:
            self.completed_on = None
        super().save(*args, **kwargs)


@receiver(post_delete, sender=InterestItem)
def delete_image_file(sender, instance, **kwargs):
    if instance.image:
        instance.image.delete(save=False)
