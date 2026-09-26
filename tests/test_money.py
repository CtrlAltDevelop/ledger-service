from decimal import Decimal

import pytest

from ledger import money


@pytest.mark.parametrize(
    ("amount", "scale", "expected"),
    [
        ("12.50", 2, True),
        ("12.5", 2, True),
        ("12.505", 2, False),
        ("100", 0, True),
        ("0.1", 0, False),
        ("0.000000000000000001", 18, True),
    ],
)
def test_fits_scale(amount: str, scale: int, expected: bool) -> None:
    assert money.fits_scale(Decimal(amount), scale) is expected


def test_quantize_keeps_every_digit_of_a_twenty_digit_amount() -> None:
    # The default 28-digit context would raise InvalidOperation here.
    amount = Decimal("12345678901234567890.123456789012345678")

    assert money.quantize(amount, 18) == amount


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("99999999999999999999.999999999999999999", True),
        ("100000000000000000000", False),
        ("Infinity", False),
        ("NaN", False),
    ],
)
def test_fits_storage(amount: str, expected: bool) -> None:
    assert money.fits_storage(Decimal(amount)) is expected


def test_format_amount_pads_to_currency_scale() -> None:
    assert money.format_amount(Decimal("5"), 2) == "5.00"
    assert money.format_amount(Decimal("-0.1"), 8) == "-0.10000000"
