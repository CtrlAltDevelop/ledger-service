"""Money arithmetic: Decimal only, never float.

Amounts are stored as ``numeric(38, 18)``. Python's default decimal context
carries 28 significant digits, which is not enough to quantize a 20-digit
amount to 18 places, so every operation here runs in a 38-digit context.
"""

from decimal import Context, Decimal, InvalidOperation

MAX_DIGITS = 38
DECIMAL_PLACES = 18
MAX_INTEGER_DIGITS = MAX_DIGITS - DECIMAL_PLACES

MONEY_CONTEXT = Context(prec=MAX_DIGITS)


def exponent(scale: int) -> Decimal:
    """The smallest unit of a currency with ``scale`` minor digits, e.g. 0.01."""
    return Decimal(1).scaleb(-scale)


def quantize(amount: Decimal, scale: int) -> Decimal:
    """Round-trip an amount to a currency's precision without losing digits."""
    return amount.quantize(exponent(scale), context=MONEY_CONTEXT)


def fits_scale(amount: Decimal, scale: int) -> bool:
    """Whether ``amount`` is exactly representable in a currency's minor unit."""
    try:
        return quantize(amount, scale) == amount
    except InvalidOperation:
        return False


def fits_storage(amount: Decimal) -> bool:
    """Whether ``amount`` fits ``numeric(38, 18)`` without truncation."""
    if not amount.is_finite():
        return False
    return amount.adjusted() < MAX_INTEGER_DIGITS


def format_amount(amount: Decimal, scale: int) -> str:
    """Render an amount the way the API returns it: fixed-point, currency scale."""
    # Negating a zero balance yields Decimal("-0"); nobody wants to see "-0.00".
    return f"{quantize(amount, scale) + 0:f}"
