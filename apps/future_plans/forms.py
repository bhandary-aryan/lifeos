from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.accounts.models import CURRENCY_CHOICES
from apps.finance.models import SavingsGoal
from apps.goals.forms import INPUT, SELECT, TEXTAREA, date_input
from apps.goals.models import Goal

from .models import FuturePlan, PlanAction


class FuturePlanForm(forms.ModelForm):
    class Meta:
        model = FuturePlan
        fields = ["title", "category", "description", "priority", "status", "target_date", "estimated_cost", "currency", "goal", "savings_goal"]
        widgets = {"title": forms.TextInput(attrs={"class": INPUT}), "category": forms.Select(attrs={"class": SELECT}),
                   "description": forms.Textarea(attrs={"class": TEXTAREA, "rows": 3}), "priority": forms.Select(attrs={"class": SELECT}),
                   "status": forms.Select(attrs={"class": SELECT}), "target_date": date_input(),
                   "estimated_cost": forms.NumberInput(attrs={"class": INPUT, "step": "0.01", "min": "0.01"}),
                   "currency": forms.Select(attrs={"class": SELECT}), "goal": forms.Select(attrs={"class": SELECT}),
                   "savings_goal": forms.Select(attrs={"class": SELECT})}
        labels = {"goal": "Linked goal (optional)", "savings_goal": "Linked savings goal (optional)", "estimated_cost": "Estimated total cost"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner = user
        if not self.instance.pk:
            self.fields["currency"].initial = user.profile.currency
        self.fields["goal"].queryset = Goal.objects.filter(owner=user)
        free = Q(future_plan__isnull=True) | Q(pk=self.instance.savings_goal_id or 0)
        self.fields["savings_goal"].queryset = SavingsGoal.objects.filter(owner=user).filter(free)
        for f in ("goal", "savings_goal"):
            self.fields[f].required = False


class ActionForm(forms.ModelForm):
    class Meta:
        model = PlanAction
        fields = ["title", "notes", "due_date"]
        widgets = {"title": forms.TextInput(attrs={"class": INPUT}), "notes": forms.TextInput(attrs={"class": INPUT}), "due_date": date_input()}

    def __init__(self, *args, user, plan, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner, self.instance.plan = user, plan


class AffordabilityForm(forms.Form):
    cost = forms.DecimalField(min_value=Decimal("0.01"), max_digits=14, decimal_places=2, label="Estimated total cost",
                              widget=forms.NumberInput(attrs={"class": INPUT, "step": "0.01"}))
    saved = forms.DecimalField(min_value=Decimal("0"), max_digits=14, decimal_places=2, required=False, label="Already saved for it",
                               widget=forms.NumberInput(attrs={"class": INPUT, "step": "0.01"}))
    monthly_available = forms.DecimalField(min_value=Decimal("0"), max_digits=14, decimal_places=2, required=False,
                                           label="You could set aside each month", widget=forms.NumberInput(attrs={"class": INPUT, "step": "0.01"}))
    target_date = forms.DateField(required=False, label="Target date (optional)", widget=date_input())
