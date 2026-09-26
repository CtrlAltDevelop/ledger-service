from decimal import Decimal

import pytest

from ledger import errors
from ledger.models import ApiClient, TransactionKind
from ledger.services import payments, reversals
from tests.conftest import AccountFactory, balance_of, fund


def test_reversal_negates_every_leg_and_links_back(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    alice, bob = make_account(), make_account()
    fund(alice, "10")
    original = payments.transfer(
        client=api_client_record,
        source_id=alice.id,
        destination_id=bob.id,
        amount=Decimal("4"),
        currency="USD",
    )

    reversal = reversals.reverse(client=api_client_record, transaction_id=original.id)

    assert reversal.kind == TransactionKind.REVERSAL
    assert reversal.reverses_id == original.id
    assert balance_of(alice) == Decimal("10")
    assert balance_of(bob) == 0
    # The original is untouched: history shows both.
    assert original.entries.count() == 2


def test_a_transaction_can_be_reversed_once(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet = make_account()
    fund(wallet, "5")
    deposit_id = wallet.entries.get().transaction_id
    reversals.reverse(client=api_client_record, transaction_id=deposit_id)

    with pytest.raises(errors.AlreadyReversed):
        reversals.reverse(client=api_client_record, transaction_id=deposit_id)


def test_a_reversal_cannot_be_reversed(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet = make_account()
    fund(wallet, "5")
    reversal = reversals.reverse(
        client=api_client_record, transaction_id=wallet.entries.get().transaction_id
    )

    with pytest.raises(errors.NotReversible):
        reversals.reverse(client=api_client_record, transaction_id=reversal.id)


def test_reversing_a_spent_deposit_is_refused(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet = make_account()
    fund(wallet, "5")
    deposit_id = wallet.entries.get().transaction_id
    payments.withdraw(
        client=api_client_record, account_id=wallet.id, amount=Decimal("5"), currency="USD"
    )

    with pytest.raises(errors.InsufficientFunds):
        reversals.reverse(client=api_client_record, transaction_id=deposit_id)
