import zoneinfo

from django.core.validators import MaxValueValidator, MinValueValidator
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

CURRENCY_CHOICES = [
    ("GBP", "GBP – British pound (£)"),
    ("USD", "USD – US dollar ($)"),
    ("EUR", "EUR – Euro (€)"),
    ("NPR", "NPR – Nepalese rupee (Rs)"),
    ("INR", "INR – Indian rupee (₹)"),
    ("AUD", "AUD – Australian dollar (A$)"),
    ("CAD", "CAD – Canadian dollar (C$)"),
]

TIMEZONE_CHOICES = sorted((tz, tz) for tz in zoneinfo.available_timezones() if "/" in tz or tz == "UTC")

LIFE_AREA_CHOICES = [
    ("career", "Career"),
    ("education", "Education"),
    ("financial", "Financial Stability"),
    ("health", "Health"),
    ("relationships", "Relationships"),
    ("home", "Home"),
    ("growth", "Personal Growth"),
    ("creativity", "Creativity"),
    ("travel", "Travel"),
]


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("email address", max_length=254, unique=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        constraints = [
            # Email is unique regardless of letter case.
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]

    def __str__(self):
        return self.email

    def get_short_name(self):
        profile = getattr(self, "profile", None)
        if profile and profile.display_name:
            return profile.display_name
        return self.email.split("@")[0]


class Profile(models.Model):
    class Theme(models.TextChoices):
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"
        SYSTEM = "system", "Match my device"

    class WeekStart(models.IntegerChoices):
        MONDAY = 0, "Monday"
        SUNDAY = 6, "Sunday"
        SATURDAY = 5, "Saturday"

    class DateFormat(models.TextChoices):
        DMY = "d/m/Y", "31/12/2026"
        MDY = "m/d/Y", "12/31/2026"
        YMD = "Y-m-d", "2026-12-31"
        LONG = "j M Y", "31 Dec 2026"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=60, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="GBP")
    timezone = models.CharField(max_length=64, choices=TIMEZONE_CHOICES, default="Europe/London")
    date_format = models.CharField(max_length=10, choices=DateFormat.choices, default=DateFormat.DMY)
    week_start = models.PositiveSmallIntegerField(choices=WeekStart.choices, default=WeekStart.MONDAY)
    theme = models.CharField(max_length=10, choices=Theme.choices, default=Theme.SYSTEM)
    daily_available_minutes = models.PositiveSmallIntegerField(
        default=480,
        validators=[MinValueValidator(30), MaxValueValidator(1440)],
        help_text="Time you realistically have for planned tasks each day.",
    )
    life_areas = models.JSONField(default=list, blank=True)
    onboarding_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(week_start__gte=0, week_start__lte=6),
                name="profile_week_start_range",
            ),
        ]

    def __str__(self):
        return f"Profile<{self.user_id}>"
