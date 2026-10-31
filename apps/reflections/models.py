"""Personal reflection records. Text is stored as plain text in the database (NOT end-to-end encrypted);
it is private to the owner in the app, never shown on the dashboard, and never included in other users' queries."""
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import OwnedModel

REVIEW_FIELDS = ["accomplishments", "goals_progressed", "obstacles", "lessons", "habits_went_well",
                 "financial_observations", "postponed", "next_priorities"]


class WeeklyReview(OwnedModel):
    week_start = models.DateField(help_text="First day of the reviewed week, in the user's week-start convention.")
    accomplishments = models.TextField(blank=True, max_length=4000)
    goals_progressed = models.TextField(blank=True, max_length=4000)
    obstacles = models.TextField(blank=True, max_length=4000)
    lessons = models.TextField(blank=True, max_length=4000)
    habits_went_well = models.TextField(blank=True, max_length=4000)
    financial_observations = models.TextField(blank=True, max_length=4000)
    postponed = models.TextField(blank=True, max_length=4000)
    next_priorities = models.TextField(blank=True, max_length=4000)

    class Meta:
        ordering = ["-week_start"]
        constraints = [models.UniqueConstraint(fields=["owner", "week_start"], name="review_one_per_owner_week")]

    def __str__(self):
        return f"Review for week of {self.week_start}"

    @property
    def filled_sections(self):
        return sum(1 for f in REVIEW_FIELDS if getattr(self, f).strip())

    def clean(self):
        if not self.filled_sections:
            raise ValidationError("Write something in at least one section, or leave this week unreviewed.")


class JournalEntry(OwnedModel):
    date = models.DateField()
    title = models.CharField(max_length=160, blank=True)
    body = models.TextField(max_length=20000)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["owner", "date"])]
        verbose_name_plural = "journal entries"

    def __str__(self):
        return self.title or f"Entry {self.date}"

    @property
    def word_count(self):
        return len(self.body.split())

    def clean(self):
        if not (self.body or "").strip():
            raise ValidationError({"body": "An entry can't be empty."})
