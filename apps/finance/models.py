"""Finance data model.

Money rules (see also finance/services.py):
- Every amount is a Decimal(14, 2); no floats anywhere.
- Account balances are DERIVED from transactions, never stored, so edits and
  deletes can never leave a stale or double-counted balance.
- A transfer is ONE row with a source and a destination: not income, not spending.
- A debt payment is ONE row: total cash out = principal + interest. Principal
  reduces the debt; only the interest portion is reported as spending.
- Accounts, debts and linked transfers use RESTRICT, not PROTECT: they cannot be
  deleted on their own while in use, but deleting the whole user still cascades
  (PROTECT would block that even though the protecting rows are deleted too).
- Savings contributions are earmarks (optionally linked to a real transfer).
  They are never expenses.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from apps.accounts.models import CURRENCY_CHOICES
from apps.core.models import OwnedModel, Priority

MONEY = {"max_digits": 14, "decimal_places": 2}
ZERO = Decimal("0.00")
CENT = Decimal("0.01")
INTEREST_CATEGORY_NAME = "Debt interest"


class Account(OwnedModel):
    class Type(models.TextChoices):
        CURRENT = "current", "Current account"
        SAVINGS = "savings", "Savings account"
        CASH = "cash", "Cash"
        CREDIT_CARD = "credit_card", "Credit card"
        INVESTMENT = "investment", "Investment account"
        OTHER = "other", "Other"

    name = models.CharField(max_length=80)
    account_type = models.CharField(max_length=15, choices=Type.choices, default=Type.CURRENT)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="GBP")
    opening_balance = models.DecimalField(
        default=ZERO, help_text="Balance before your first recorded transaction. Use a negative number for money owed (e.g. a credit card).", **MONEY)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_active", "name"]
        constraints = [models.UniqueConstraint(Lower("name"), "owner", name="account_owner_name_ci_unique")]

    def __str__(self):
        return f"{self.name} ({self.currency})"


class Category(OwnedModel):
    class Kind(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    name = models.CharField(max_length=60)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    is_system = models.BooleanField(default=False, help_text="Managed by the app (e.g. debt interest); cannot be deleted.")

    class Meta:
        ordering = ["kind", "name"]
        verbose_name_plural = "categories"
        constraints = [models.UniqueConstraint(Lower("name"), "owner", "kind", name="category_owner_kind_name_ci_unique")]

    def __str__(self):
        return self.name


class Debt(OwnedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAID_OFF = "paid_off", "Paid off"
        CLOSED = "closed", "Closed"

    creditor = models.CharField(max_length=120)
    description = models.CharField(max_length=200, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="GBP")
    original_amount = models.DecimalField(help_text="Principal borrowed at the start.", **MONEY)
    interest_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Annual interest rate in percent. Used only for estimates.")
    minimum_payment = models.DecimalField(default=ZERO, **MONEY)
    due_day = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(31)], help_text="Day of the month a payment is due.")
    start_date = models.DateField()
    expected_payoff_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "creditor"]
        constraints = [
            models.CheckConstraint(condition=Q(original_amount__gt=0), name="debt_original_positive"),
            models.CheckConstraint(condition=Q(interest_rate__gte=0, interest_rate__lte=100), name="debt_rate_0_100"),
            models.CheckConstraint(condition=Q(minimum_payment__gte=0), name="debt_min_payment_nonneg"),
            models.CheckConstraint(condition=Q(due_day__isnull=True) | Q(due_day__gte=1, due_day__lte=31), name="debt_due_day_1_31"),
            models.CheckConstraint(
                condition=Q(expected_payoff_date__isnull=True) | Q(expected_payoff_date__gte=F("start_date")),
                name="debt_payoff_not_before_start"),
        ]

    def __str__(self):
        return self.creditor


class DebtDrawdown(OwnedModel):
    """Additional borrowing on an existing debt (raises outstanding principal)."""

    debt = models.ForeignKey(Debt, on_delete=models.CASCADE, related_name="drawdowns")
    date = models.DateField()
    amount = models.DecimalField(**MONEY)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="drawdown_amount_positive")]

    def clean(self):
        if self.debt_id and self.debt.owner_id != self.owner_id:
            raise ValidationError("Choose one of your own debts.")


class Transaction(OwnedModel):
    class Type(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        TRANSFER = "transfer", "Transfer"
        DEBT_PAYMENT = "debt_payment", "Debt payment"

    type = models.CharField(max_length=15, choices=Type.choices)
    date = models.DateField()
    amount = models.DecimalField(help_text="Total amount. For a debt payment this is the total cash paid.", **MONEY)
    account = models.ForeignKey(Account, on_delete=models.RESTRICT, related_name="transactions", help_text="Account money leaves (transfer/expense/payment) or arrives in (income).")
    destination_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.RESTRICT, related_name="incoming_transfers")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions")
    description = models.CharField(max_length=200, blank=True)
    debt = models.ForeignKey(Debt, null=True, blank=True, on_delete=models.RESTRICT, related_name="payments")
    principal_amount = models.DecimalField(default=ZERO, **MONEY)
    interest_amount = models.DecimalField(default=ZERO, **MONEY)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="tx_amount_positive"),
            models.CheckConstraint(condition=Q(principal_amount__gte=0, interest_amount__gte=0), name="tx_parts_nonneg"),
            models.CheckConstraint(
                condition=~Q(type__in=["income", "expense"]) | Q(destination_account__isnull=True, debt__isnull=True, principal_amount=0, interest_amount=0),
                name="tx_income_expense_shape"),
            models.CheckConstraint(
                condition=~Q(type="transfer") | (Q(destination_account__isnull=False, category__isnull=True, debt__isnull=True, principal_amount=0, interest_amount=0) & ~Q(destination_account=F("account"))),
                name="tx_transfer_shape"),
            models.CheckConstraint(
                condition=~Q(type="debt_payment") | (Q(debt__isnull=False, destination_account__isnull=True) & Q(amount=F("principal_amount") + F("interest_amount"))),
                name="tx_debt_payment_shape"),
        ]
        indexes = [
            models.Index(fields=["owner", "date"]),
            models.Index(fields=["owner", "type", "date"]),
            models.Index(fields=["owner", "account", "date"]),
            models.Index(fields=["owner", "category", "date"]),
        ]

    def __str__(self):
        return f"{self.get_type_display()} {self.amount} on {self.date}"

    @property
    def is_linked_to_savings(self):
        return hasattr(self, "savings_contribution")

    def clean(self):
        """Stateless rules for each transaction type. State-dependent rules live in services."""
        errors = {}
        owner_id = self.owner_id
        if not self.account_id or self.amount is None:
            return  # required-field errors are reported by the form/field validation
        if self.amount <= 0:
            errors["amount"] = "Amount must be greater than zero."
        for field, obj in (("account", self.account if self.account_id else None),
                           ("destination_account", self.destination_account if self.destination_account_id else None),
                           ("category", self.category if self.category_id else None),
                           ("debt", self.debt if self.debt_id else None)):
            if obj is not None and obj.owner_id != owner_id:
                errors[field] = "Choose one of your own records."
        if errors:
            raise ValidationError(errors)

        t = self.type
        if t in (self.Type.INCOME, self.Type.EXPENSE):
            if self.destination_account_id or self.debt_id:
                raise ValidationError("Income and expenses cannot have a destination account or a debt.")
            if self.principal_amount or self.interest_amount:
                raise ValidationError("Principal and interest apply only to debt payments.")
            if self.category_id:
                want = Category.Kind.INCOME if t == self.Type.INCOME else Category.Kind.EXPENSE
                if self.category.kind != want:
                    raise ValidationError({"category": f"Choose an {want} category for this transaction."})
        elif t == self.Type.TRANSFER:
            if not self.destination_account_id:
                raise ValidationError({"destination_account": "Choose the account the money moves to."})
            if self.destination_account_id == self.account_id:
                raise ValidationError({"destination_account": "A transfer needs two different accounts."})
            if self.account.currency != self.destination_account.currency:
                raise ValidationError("Transfers between accounts in different currencies are not supported; amounts are never silently converted.")
            if self.category_id or self.debt_id or self.principal_amount or self.interest_amount:
                raise ValidationError("A transfer cannot have a category, debt, principal or interest.")
        elif t == self.Type.DEBT_PAYMENT:
            if not self.debt_id:
                raise ValidationError({"debt": "Choose the debt this payment is for."})
            if self.destination_account_id:
                raise ValidationError("A debt payment has no destination account.")
            if self.account.currency != self.debt.currency:
                raise ValidationError("The paying account and the debt must use the same currency.")
            if self.principal_amount is None or self.interest_amount is None:
                raise ValidationError("Enter both the principal and interest portions (use 0 if none).")
            if self.principal_amount + self.interest_amount != self.amount:
                raise ValidationError(
                    f"Principal ({self.principal_amount}) plus interest ({self.interest_amount}) must equal the total paid ({self.amount}).")
        else:
            raise ValidationError({"type": "Unknown transaction type."})

    def save(self, *args, **kwargs):
        # Interest is reported under a managed category so budgets/breakdowns include it exactly once.
        if self.type == self.Type.DEBT_PAYMENT:
            if self.interest_amount and self.interest_amount > 0:
                from .services import interest_category

                self.category = interest_category(self.owner)
            else:
                self.category = None
        super().save(*args, **kwargs)


class SavingsGoal(OwnedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ACHIEVED = "achieved", "Achieved"
        PAUSED = "paused", "Paused"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=120)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="GBP")
    target_amount = models.DecimalField(**MONEY)
    target_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)
    # A link to a future plan is added in Phase 4 (future_plans does not exist yet).

    class Meta:
        ordering = ["status", "target_date", "name"]
        constraints = [models.CheckConstraint(condition=Q(target_amount__gt=0), name="savings_target_positive")]

    def __str__(self):
        return self.name


class SavingsContribution(OwnedModel):
    goal = models.ForeignKey(SavingsGoal, on_delete=models.CASCADE, related_name="contributions")
    date = models.DateField()
    amount = models.DecimalField(**MONEY)
    note = models.CharField(max_length=200, blank=True)
    transfer = models.OneToOneField(Transaction, null=True, blank=True, on_delete=models.RESTRICT, related_name="savings_contribution",
                                    help_text="Optional real account-to-account transfer that moved this money.")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="contribution_amount_positive")]

    def clean(self):
        if self.goal_id and self.goal.owner_id != self.owner_id:
            raise ValidationError("Choose one of your own savings goals.")


class Budget(OwnedModel):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="budgets")
    year = models.PositiveSmallIntegerField(validators=[MinValueValidator(2000), MaxValueValidator(2200)])
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="GBP")
    limit_amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["year", "month", "category__name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "category", "year", "month", "currency"], name="budget_unique_per_category_month"),
            models.CheckConstraint(condition=Q(limit_amount__gt=0), name="budget_limit_positive"),
            models.CheckConstraint(condition=Q(month__gte=1, month__lte=12), name="budget_month_1_12"),
        ]

    def clean(self):
        if self.category_id:
            if self.category.owner_id != self.owner_id:
                raise ValidationError({"category": "Choose one of your own categories."})
            if self.category.kind != Category.Kind.EXPENSE:
                raise ValidationError({"category": "Budgets apply to expense categories."})
