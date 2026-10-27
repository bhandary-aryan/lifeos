from django import forms

from apps.goals.models import Goal, LifeArea, Milestone
from apps.goals.forms import INPUT, SELECT, TEXTAREA, date_input

from .models import Habit, Task

WEEKDAY_CHOICES = [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")]


class MilestoneChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.goal.title} › {obj.title}"


class _TaskFormBase(forms.ModelForm):
    def _limit_choices(self, user):
        self.instance.owner = user
        self.fields["goal"].queryset = Goal.objects.filter(owner=user).exclude(
            status__in=[Goal.Status.COMPLETED, Goal.Status.ABANDONED]) | Goal.objects.filter(
            owner=user, pk=getattr(self.instance, "goal_id", None) or 0)
        self.fields["milestone"] = MilestoneChoiceField(
            queryset=Milestone.objects.filter(owner=user).select_related("goal"),
            required=False, widget=forms.Select(attrs={"class": SELECT}))
        self.fields["goal"].required = False


class TaskForm(_TaskFormBase):
    start_at = forms.DateTimeField(
        required=False, label="Time block start", input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": INPUT}, format="%Y-%m-%dT%H:%M"),
    )

    class Meta:
        model = Task
        fields = ["title", "description", "due_date", "start_at", "estimated_minutes", "priority", "status", "goal", "milestone", "weight"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT}),
            "description": forms.Textarea(attrs={"class": TEXTAREA, "rows": 3}),
            "due_date": date_input(),
            "estimated_minutes": forms.NumberInput(attrs={"class": INPUT, "min": 0, "max": 1440, "step": 5}),
            "priority": forms.Select(attrs={"class": SELECT}),
            "status": forms.Select(attrs={"class": SELECT}),
            "goal": forms.Select(attrs={"class": SELECT}),
            "weight": forms.NumberInput(attrs={"class": INPUT, "min": 1, "max": 1000}),
        }
        labels = {"estimated_minutes": "Estimated duration (minutes)", "weight": "Weight (for task-based goal progress)"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self._limit_choices(user)


class QuickTaskForm(_TaskFormBase):
    """Small form for the quick-add bar and goal pages. Goal/milestone are optional hidden fields."""

    class Meta:
        model = Task
        fields = ["title", "due_date", "priority", "estimated_minutes", "goal", "milestone"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT, "placeholder": "Add a task…", "aria-label": "Task title"}),
            "due_date": date_input(),
            "priority": forms.Select(attrs={"class": SELECT}),
            "estimated_minutes": forms.NumberInput(attrs={"class": INPUT, "min": 0, "max": 1440, "placeholder": "min"}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self._limit_choices(user)
        self.fields["goal"].widget = forms.HiddenInput()
        self.fields["milestone"].widget = forms.HiddenInput()


class HabitForm(forms.ModelForm):
    weekdays = forms.TypedMultipleChoiceField(
        choices=WEEKDAY_CHOICES, coerce=int, required=False, widget=forms.CheckboxSelectMultiple,
        label="Days (for 'Selected weekdays')",
    )

    class Meta:
        model = Habit
        fields = ["name", "description", "frequency", "weekly_target", "start_date", "end_date", "category"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT}),
            "description": forms.Textarea(attrs={"class": TEXTAREA, "rows": 2}),
            "frequency": forms.Select(attrs={"class": SELECT}),
            "weekly_target": forms.NumberInput(attrs={"class": INPUT, "min": 1, "max": 7}),
            "start_date": date_input(),
            "end_date": date_input(),
            "category": forms.Select(attrs={"class": SELECT}),
        }
        labels = {"weekly_target": "Target per week (for 'Times per week')", "category": "Life area"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner = user
        self.fields["category"].queryset = LifeArea.objects.filter(owner=user)
        # Only meaningful for "times per week" habits, so blank falls back to the default.
        self.fields["weekly_target"].required = False
        if self.instance.pk:
            self.initial["weekdays"] = self.instance.weekdays

    def clean(self):
        cleaned = super().clean()
        freq = cleaned.get("frequency")
        if cleaned.get("weekly_target") is None and "weekly_target" not in self.errors:
            cleaned["weekly_target"] = 3
        days = sorted(set(cleaned.get("weekdays") or []))
        if freq == Habit.Frequency.WEEKDAYS and not days:
            self.add_error("weekdays", "Pick at least one weekday.")
        self.instance.weekdays = days if freq == Habit.Frequency.WEEKDAYS else []
        return cleaned
