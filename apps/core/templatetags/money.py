from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€", "NPR": "Rs ", "INR": "₹", "AUD": "A$", "CAD": "C$"}


@register.filter
def money(value, currency="GBP"):
    """Format a Decimal amount with its currency symbol: £1,234.50 / -£12.00."""
    try:
        amount = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return ""
    sign = "-" if amount < 0 else ""
    return f"{sign}{SYMBOLS.get(currency, currency + ' ')}{abs(amount):,.2f}"


@register.filter
def abs_val(value):
    try:
        return abs(Decimal(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
