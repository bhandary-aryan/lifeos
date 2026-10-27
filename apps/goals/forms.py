from django import forms

from .models import Goal, LifeArea, Milestone

INPUT = "input input-bordered w-full"
SELECT = "select select-bordered w-full"
TEXTAREA = "textarea textarea-bordered w-full"


def date_input():
    return forms.DateInput(attrs={"type": "date", "class": INPUT}, format="%Y-%m-%d")


class GoalForm(forms.ModelForm):
    class Meta:
        model = Goal
        fields = [
            "title", "description", "life_area", "priority", "status", "start_date", "target_date",
            "progress_mode", "manual_progress", "notes",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT}),
            "description": forms.Textarea(attrs={"class": TEXTAREA, "rows": 3}),
            "life_area": forms.Select(attrs={"class": SELECT}),
            "priority": forms.Select(attrs={"class": SELECT}),
            "status": forms.Select(attrs={"class": SELECT}),
            "start_date": date_input(),
            "target_date": date_input(),
            "progress_mode": forms.Select(attrs={"class": SELECT}),
            "manual_progress": forms.NumberInput(attrs={"class": INPUT, "min": 0, "max": 100}),
            "notes": forms.Textarea(attrs={"class": TEXTAREA, "rows": 3}),
        }
        labels = {"manual_progress": "Manual progress (%)", "life_area": "Life area", "notes": "Notes / reflection"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner = user
        self.fields["life_area"].queryset = LifeArea.objects.filter(owner=user)
        self.fields["life_area"].required = False


class MilestoneForm(forms.ModelForm):
    class Meta:
        model = Milestone
        fields = ["title", "description", "due_date", "status", "order", "weight"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT}),
            "description": forms.Textarea(attrs={"class": TEXTAREA, "rows": 2}),
            "due_date": date_input(),
            "status": forms.Select(attrs={"class": SELECT}),
            "order": forms.NumberInput(attrs={"class": INPUT, "min": 0}),
            "weight": forms.NumberInput(attrs={"class": INPUT, "min": 1, "max": 1000}),
        }
        help_texts = {"weight": "Relative importance when progress is calculated from milestones (1 or more)."}

    def __init__(self, *args, user, goal=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner = user
        if goal is not None:
            self.instance.goal = goal


class LifeAreaForm(forms.ModelForm):
    class Meta:
        model = LifeArea
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"class": INPUT, "placeholder": "e.g. Volunteering"})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance.owner = user

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if LifeArea.objects.filter(owner=self.user, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("You already have a life area with that name.")
        return name

    def validate_unique(self):
        pass  # handled in clean_name (the DB constraint is the backstop)
