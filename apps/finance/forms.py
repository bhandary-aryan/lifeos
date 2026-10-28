from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.accounts.models import CURRENCY_CHOICES
from apps.goals.forms import INPUT, SELECT, TEXTAREA, date_input

from .models import (
    Account, Budget, Category, Debt, DebtDrawdown, SavingsContribution, SavingsGoal, Transaction,
)

ZERO = Decimal("0.00")
MONEY_INPUT = {"class": INPUT, "step": "0.01", "inputmode": "decimal"}


def money_widget(**extra):
    return forms.NumberInput(attrs={**MONEY_INPUT, **extra})


class _Owned(forms.ModelForm):
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance.owner = user


class AccountForm(_Owned):
    class Meta:
        model = Account
        fields = ["name", "account_type", "currency", "opening_balance", "is_active"]
        widgets = {"name": forms.TextInput(attrs={"class": INPUT}), "account_type": forms.Select(attrs={"class": SELECT}),
                   "currency": forms.Select(attrs={"class": SELECT}), "opening_balance": money_widget()}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not self.instance.pk:
            self.fields["currency"].initial = user.profile.currency

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Account.objects.filter(owner=self.user, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("You already have an account with that name.")
        return name

    def clean_currency(self):
        cur = self.cleaned_data["currency"]
        if self.instance.pk and cur != Account.objects.get(pk=self.instance.pk).currency:
            has = (Transaction.objects.filter(Q(account=self.instance) | Q(destination_account=self.instance)).exists())
            if has:
                raise forms.ValidationError("Currency can't change once an account has transactions; amounts are never converted.")
        return cur

    def validate_unique(self):
        pass


class CategoryForm(_Owned):
    class Meta:
        model = Category
        fields = ["name", "kind"]
        widgets = {"name": forms.TextInput(attrs={"class": INPUT}), "kind": forms.Select(attrs={"class": SELECT})}

    def clean(self):
        cleaned = super().clean()
        name, kind = (cleaned.get("name") or "").strip(), cleaned.get("kind")
        if name and kind and Category.objects.filter(owner=self.user, kind=kind, name__iexact=name).exists():
            self.add_error("name", "You already have a category with that name and type.")
        cleaned["name"] = name
        return cleaned

    def validate_unique(self):
        pass


class TransactionForm(_Owned):
    class Meta:
        model = Transaction
        fields = ["type", "date", "amount", "account", "destination_account", "category", "description",
                  "debt", "principal_amount", "interest_amount"]
        widgets = {
            "type": forms.Select(attrs={"class": SELECT, "x-model": "type"}), "date": date_input(),
            "amount": money_widget(min="0.01"),
            "account": forms.Select(attrs={"class": SELECT}), "destination_account": forms.Select(attrs={"class": SELECT}),
            "category": forms.Select(attrs={"class": SELECT}), "description": forms.TextInput(attrs={"class": INPUT}),
            "debt": forms.Select(attrs={"class": SELECT}),
            "principal_amount": money_widget(min="0"), "interest_amount": money_widget(min="0"),
        }
        labels = {"amount": "Amount (total paid, for debt payments)", "account": "Account (money leaves / arrives)",
                  "destination_account": "To account (transfers)", "principal_amount": "Principal portion",
                  "interest_amount": "Interest portion"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        keep = [self.instance.account_id, self.instance.destination_account_id]
        accounts = Account.objects.filter(owner=user).filter(Q(is_active=True) | Q(pk__in=[k for k in keep if k]))
        self.fields["account"].queryset = accounts
        self.fields["destination_account"].queryset = accounts
        self.fields["destination_account"].required = False
        self.fields["category"].queryset = Category.objects.filter(owner=user, is_system=False)
        self.fields["category"].required = False
        self.fields["debt"].queryset = Debt.objects.filter(owner=user).exclude(status=Debt.Status.CLOSED)
        self.fields["debt"].required = False
        for f in ("principal_amount", "interest_amount"):
            self.fields[f].required = False

    def clean(self):
        cleaned = super().clean()
        t = cleaned.get("type")
        if t == Transaction.Type.DEBT_PAYMENT:
            for f in ("principal_amount", "interest_amount"):
                if cleaned.get(f) is None and f not in self.errors:
                    self.add_error(f, "Enter an amount (use 0 if none).")
        else:
            for f in ("principal_amount", "interest_amount"):
                cleaned[f] = ZERO
            if t in (Transaction.Type.INCOME, Transaction.Type.EXPENSE):
                cleaned["destination_account"] = None
                cleaned["debt"] = None
            elif t == Transaction.Type.TRANSFER:
                cleaned["category"] = None
                cleaned["debt"] = None
        if t == Transaction.Type.DEBT_PAYMENT:
            cleaned["destination_account"] = None
            cleaned["category"] = None
        return cleaned


class DebtForm(_Owned):
    class Meta:
        model = Debt
        fields = ["creditor", "description", "currency", "original_amount", "interest_rate", "minimum_payment",
                  "due_day", "start_date", "expected_payoff_date", "status", "notes"]
        widgets = {
            "creditor": forms.TextInput(attrs={"class": INPUT}), "description": forms.TextInput(attrs={"class": INPUT}),
            "currency": forms.Select(attrs={"class": SELECT}), "original_amount": money_widget(min="0.01"),
            "interest_rate": forms.NumberInput(attrs={"class": INPUT, "step": "0.01", "min": 0, "max": 100}),
            "minimum_payment": money_widget(min="0"), "due_day": forms.NumberInput(attrs={"class": INPUT, "min": 1, "max": 31}),
            "start_date": date_input(), "expected_payoff_date": date_input(),
            "status": forms.Select(attrs={"class": SELECT}), "notes": forms.Textarea(attrs={"class": TEXTAREA, "rows": 3}),
        }
        labels = {"interest_rate": "Annual interest rate (%)", "due_day": "Payment due day of month"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not self.instance.pk:
            self.fields["currency"].initial = user.profile.currency

    def clean_currency(self):
        cur = self.cleaned_data["currency"]
        if self.instance.pk and cur != Debt.objects.get(pk=self.instance.pk).currency and self.instance.payments.exists():
            raise forms.ValidationError("Currency can't change once payments are recorded.")
        return cur


class DrawdownForm(_Owned):
    class Meta:
        model = DebtDrawdown
        fields = ["date", "amount", "note"]
        widgets = {"date": date_input(), "amount": money_widget(min="0.01"), "note": forms.TextInput(attrs={"class": INPUT})}
        labels = {"amount": "Additional amount borrowed"}

    def __init__(self, *args, user, debt=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if debt is not None:
            self.instance.debt = debt


class SavingsGoalForm(_Owned):
    class Meta:
        model = SavingsGoal
        fields = ["name", "currency", "target_amount", "target_date", "priority", "status", "notes"]
        widgets = {"name": forms.TextInput(attrs={"class": INPUT}), "currency": forms.Select(attrs={"class": SELECT}),
                   "target_amount": money_widget(min="0.01"), "target_date": date_input(),
                   "priority": forms.Select(attrs={"class": SELECT}), "status": forms.Select(attrs={"class": SELECT}),
                   "notes": forms.Textarea(attrs={"class": TEXTAREA, "rows": 3})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not self.instance.pk:
            self.fields["currency"].initial = user.profile.currency

    def clean_currency(self):
        cur = self.cleaned_data["currency"]
        if self.instance.pk and cur != SavingsGoal.objects.get(pk=self.instance.pk).currency and self.instance.contributions.exists():
            raise forms.ValidationError("Currency can't change once contributions are recorded.")
        return cur


class ContributionForm(_Owned):
    from_account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False, label="Move money from (optional)",
                                          widget=forms.Select(attrs={"class": SELECT}))
    to_account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False, label="Into account (optional)",
                                        widget=forms.Select(attrs={"class": SELECT}))

    class Meta:
        model = SavingsContribution
        fields = ["date", "amount", "note"]
        widgets = {"date": date_input(), "amount": money_widget(min="0.01"), "note": forms.TextInput(attrs={"class": INPUT})}

    def __init__(self, *args, user, goal, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.instance.goal = goal
        qs = Account.objects.filter(owner=user, currency=goal.currency, is_active=True)
        self.fields["from_account"].queryset = qs
        self.fields["to_account"].queryset = qs
        if self.instance.pk and self.instance.transfer_id:
            self.initial["from_account"] = self.instance.transfer.account_id
            self.initial["to_account"] = self.instance.transfer.destination_account_id
            self.fields["from_account"].queryset = Account.objects.filter(owner=user, currency=goal.currency)
            self.fields["to_account"].queryset = Account.objects.filter(owner=user, currency=goal.currency)

    def clean(self):
        cleaned = super().clean()
        if bool(cleaned.get("from_account")) != bool(cleaned.get("to_account")):
            raise forms.ValidationError("Choose both accounts to move money, or leave both empty to just record the contribution.")
        if cleaned.get("from_account") and cleaned.get("from_account") == cleaned.get("to_account"):
            self.add_error("to_account", "Choose two different accounts.")
        return cleaned


class BudgetForm(_Owned):
    class Meta:
        model = Budget
        fields = ["category", "year", "month", "currency", "limit_amount"]
        widgets = {"category": forms.Select(attrs={"class": SELECT}), "year": forms.NumberInput(attrs={"class": INPUT}),
                   "month": forms.Select(attrs={"class": SELECT}, choices=[(i, __import__("calendar").month_name[i]) for i in range(1, 13)]),
                   "currency": forms.Select(attrs={"class": SELECT}), "limit_amount": money_widget(min="0.01")}
        labels = {"limit_amount": "Monthly limit"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(owner=user, kind=Category.Kind.EXPENSE)
        if not self.instance.pk:
            self.fields["currency"].initial = user.profile.currency

    def clean(self):
        cleaned = super().clean()
        keys = {k: cleaned.get(k) for k in ("category", "year", "month", "currency")}
        if all(keys.values()):
            dup = Budget.objects.filter(owner=self.user, category=keys["category"], year=keys["year"],
                                        month=keys["month"], currency=keys["currency"]).exclude(pk=self.instance.pk)
            if dup.exists():
                raise forms.ValidationError("You already have a budget for that category, month and currency.")
        return cleaned

    def validate_unique(self):
        pass


class PlannerForm(forms.Form):
    currency = forms.ChoiceField(choices=CURRENCY_CHOICES, widget=forms.Select(attrs={"class": SELECT}))
    monthly_budget = forms.DecimalField(min_value=Decimal("0.01"), max_digits=14, decimal_places=2,
                                        label="Total you can pay towards debts each month", widget=money_widget(min="0.01"))
