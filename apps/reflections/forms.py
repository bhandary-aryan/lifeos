from django import forms

from apps.goals.forms import INPUT, TEXTAREA, date_input

from .models import JournalEntry, WeeklyReview

PROMPTS = {
    "accomplishments": "What did I accomplish?", "goals_progressed": "Which goals moved forward?", "obstacles": "What got in the way?",
    "lessons": "What did I learn?", "habits_went_well": "Which habits went well?", "financial_observations": "Anything I noticed about money?",
    "postponed": "What did I postpone (and is that okay)?", "next_priorities": "What matters most next week?",
}


class WeeklyReviewForm(forms.ModelForm):
    class Meta:
        model = WeeklyReview
        fields = list(PROMPTS)
        widgets = {f: forms.Textarea(attrs={"class": TEXTAREA, "rows": 3, "maxlength": 4000}) for f in PROMPTS}
        labels = PROMPTS

    def __init__(self, *args, user, week_start, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner, self.instance.week_start = user, week_start
        for f in self.fields.values():
            f.required = False


class JournalForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ["date", "title", "body"]
        widgets = {"date": date_input(), "title": forms.TextInput(attrs={"class": INPUT}),
                   "body": forms.Textarea(attrs={"class": TEXTAREA, "rows": 14, "maxlength": 20000})}
        labels = {"title": "Title (optional)", "body": "What's on your mind?"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner = user
