from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.forms import AuthenticationForm

from .models import LIFE_AREA_CHOICES, Profile, User

INPUT = "input input-bordered w-full"
SELECT = "select select-bordered w-full"


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email", "class": INPUT}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "class": INPUT}),
    )

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class RegistrationForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": INPUT}),
        help_text="At least 10 characters. Avoid common or all-numeric passwords.",
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": INPUT}),
    )

    class Meta:
        model = User
        fields = ["email"]
        widgets = {"email": forms.EmailInput(attrs={"autocomplete": "email", "class": INPUT})}

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        elif p1:
            candidate = User(email=cleaned.get("email", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self, commit=True):
        return User.objects.create_user(
            email=self.cleaned_data["email"], password=self.cleaned_data["password1"]
        )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["display_name"]
        widgets = {"display_name": forms.TextInput(attrs={"class": INPUT, "autocomplete": "nickname"})}


class PreferencesForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["currency", "timezone", "date_format", "week_start", "daily_available_minutes", "theme"]
        widgets = {
            **{f: forms.Select(attrs={"class": SELECT}) for f in ["currency", "timezone", "date_format", "week_start", "theme"]},
            "daily_available_minutes": forms.NumberInput(attrs={"class": INPUT, "min": 30, "max": 1440, "step": 15}),
        }
        labels = {"daily_available_minutes": "Available minutes per day"}


class LifeAreasForm(forms.Form):
    life_areas = forms.MultipleChoiceField(
        choices=LIFE_AREA_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Areas of life you want to manage",
    )


class DeleteAccountForm(forms.Form):
    password = forms.CharField(
        label="Confirm your password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "class": INPUT}),
    )
    confirm = forms.BooleanField(
        label="I understand this permanently deletes my account and all my data.",
        error_messages={"required": "Please tick the box to confirm."},
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        pw = self.cleaned_data["password"]
        if authenticate(email=self.user.email, password=pw) is None:
            raise forms.ValidationError("That password is incorrect.")
        return pw
