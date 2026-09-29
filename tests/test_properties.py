"""Property tests: whatever sequence of operations runs, the invariants hold.

Hypothesis generates random programs of deposits, withdrawals, transfers,
holds, captures, releases and reversals across a few accounts and two
currencies. Many operations are refused (insufficient funds, a hold already
captured); that is fine. What must never happen is money appearing,
disappearing or going negative where it may not.
"""

from contextlib import suppress
from decimal import Decimal
from typing import Any

from django.db.models import Sum
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from ledger import errors
from ledger.models import Account, ApiClient, Entry, Hold, Transaction
from ledger.services import balances, holds, payments, reversals
from ledger.services.reconciliation import reconcile
from tests.conftest import account_factory

WALLETS = 3  # per currency
CURRENCIES = ("USD", "BTC")

amounts = st.decimals(min_value=Decimal("0.01"), max_value=Decimal(500), places=2)
wallet_index = st.integers(min_value=0, max_value=WALLETS - 1)
currency = st.sampled_from(CURRENCIES)

operation = st.one_of(
    st.tuples(st.just("deposit"), currency, wallet_index, amounts),
    st.tuples(st.just("withdraw"), currency, wallet_index, amounts),
    st.tuples(st.just("transfer"), currency, wallet_index, wallet_index, amounts),
    st.tuples(st.just("hold"), currency, wallet_index, amounts),
    st.tuples(st.just("capture"), st.integers(0, 20), wallet_index, st.booleans()),
    st.tuples(st.just("release"), st.integers(0, 20)),
    st.tuples(st.just("reverse"), st.integers(0, 40)),
)


class LedgerInvariants(TestCase):
    def setUp(self) -> None:
        self.client_record = ApiClient.objects.create(name="props", key_hash="p" * 64)
        make = account_factory(self.client_record)
        self.wallets = {code: [make(code) for _ in range(WALLETS)] for code in CURRENCIES}

    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(program=st.lists(operation, min_size=1, max_size=40))
    def test_money_is_neither_created_nor_destroyed(self, program: list[tuple[Any, ...]]) -> None:
        for op in program:
            # A refused operation must leave no trace; the checks below prove it.
            with suppress(errors.LedgerError):
                self.apply(op)

        self.assert_every_currency_sums_to_zero()
        self.assert_no_wallet_is_overdrawn()
        self.assert_snapshots_agree_with_the_plain_sum()
        report = reconcile()
        assert report.ok, report.as_dict()

    def apply(self, op: tuple[Any, ...]) -> None:
        client = self.client_record
        match op:
            case ("deposit", code, i, amount):
                payments.deposit(
                    client=client, account_id=self.wallet(code, i).id, amount=amount, currency=code
                )
            case ("withdraw", code, i, amount):
                payments.withdraw(
                    client=client, account_id=self.wallet(code, i).id, amount=amount, currency=code
                )
            case ("transfer", code, i, j, amount):
                payments.transfer(
                    client=client,
                    source_id=self.wallet(code, i).id,
                    destination_id=self.wallet(code, j).id,
                    amount=amount,
                    currency=code,
                )
            case ("hold", code, i, amount):
                holds.create_hold(
                    client=client, account_id=self.wallet(code, i).id, amount=amount, currency=code
                )
            case ("capture", n, j, partial):
                hold = self.nth(Hold.objects.order_by("created_at", "id"), n)
                if hold is None:
                    return
                code = hold.account.currency_id
                holds.capture_hold(
                    client=client,
                    hold_id=hold.id,
                    destination_id=self.wallet(code, j).id,
                    amount=(hold.amount / 2).quantize(Decimal("0.01")) if partial else None,
                )
            case ("release", n):
                hold = self.nth(Hold.objects.order_by("created_at", "id"), n)
                if hold is not None:
                    holds.release_hold(client=client, hold_id=hold.id)
            case ("reverse", n):
                txn = self.nth(Transaction.objects.order_by("created_at", "id"), n)
                if txn is not None:
                    reversals.reverse(client=client, transaction_id=txn.id)

    def wallet(self, code: str, i: int) -> Account:
        return self.wallets[code][i]

    @staticmethod
    def nth(queryset: Any, n: int) -> Any:
        rows = list(queryset)
        return rows[n % len(rows)] if rows else None

    def assert_every_currency_sums_to_zero(self) -> None:
        for code in CURRENCIES:
            total = Entry.objects.filter(currency=code).aggregate(t=Sum("amount"))["t"]
            assert (total or 0) == 0, f"{code} does not sum to zero"

    def assert_no_wallet_is_overdrawn(self) -> None:
        for account in Account.objects.filter(allow_overdraft=False):
            balance = balances.get_balance(account)
            assert balance.ledger >= 0, account
            assert balance.available >= 0, account

    def assert_snapshots_agree_with_the_plain_sum(self) -> None:
        for account in Account.objects.all():
            plain = Entry.objects.filter(account=account).aggregate(t=Sum("amount"))["t"] or 0
            assert balances.get_balance(account).ledger == plain * account.normal_sign
