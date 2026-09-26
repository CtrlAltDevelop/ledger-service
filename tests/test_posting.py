from decimal import Decimal

import pytest

from ledger import errors
from ledger.models import (
    AccountStatus,
    AccountType,
    ApiClient,
    BalanceSnapshot,
    Entry,
    OutboxEvent,
    TransactionKind,
)
from ledger.services import accounts, balances
from ledger.services.posting import Leg, post
from tests.conftest import AccountFactory, balance_of, fund


def test_post_writes_entries_and_an_outbox_event(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    cash = make_account(kind=AccountType.ASSET, allow_overdraft=True)
    equity = make_account(kind=AccountType.EQUITY)

    txn = post(
        client=api_client_record,
        kind=TransactionKind.JOURNAL,
        legs=[Leg(cash.id, Decimal("100"), "USD"), Leg(equity.id, Decimal("-100"), "USD")],
    )

    assert sorted(e.amount for e in txn.entries.all()) == [Decimal("-100"), Decimal("100")]
    assert balance_of(cash) == Decimal("100")
    assert balance_of(equity) == Decimal("100")  # credit-normal, so shown positive
    event = OutboxEvent.objects.get(aggregate_id=txn.id)
    assert event.event_type == "transaction.posted"
    assert event.payload["kind"] == "journal"


def test_unbalanced_legs_are_rejected(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    a, b = make_account(allow_overdraft=True), make_account()

    with pytest.raises(errors.UnbalancedTransaction) as caught:
        post(
            client=api_client_record,
            kind=TransactionKind.JOURNAL,
            legs=[Leg(a.id, Decimal("10"), "USD"), Leg(b.id, Decimal("-9"), "USD")],
        )

    assert caught.value.extra["imbalance"] == {"USD": "1"}
    assert not Entry.objects.exists()


def test_a_single_leg_is_rejected(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    with pytest.raises(errors.UnbalancedTransaction):
        post(
            client=api_client_record,
            kind=TransactionKind.JOURNAL,
            legs=[Leg(make_account().id, Decimal("1"), "USD")],
        )


def test_each_currency_must_balance_on_its_own(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    usd, eur = make_account(allow_overdraft=True), make_account("EUR", allow_overdraft=True)

    # Sums to zero overall, but creates 5 USD and destroys 5 EUR.
    with pytest.raises(errors.UnbalancedTransaction):
        post(
            client=api_client_record,
            kind=TransactionKind.JOURNAL,
            legs=[Leg(usd.id, Decimal("5"), "USD"), Leg(eur.id, Decimal("-5"), "EUR")],
        )


def test_leg_currency_must_match_the_account(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    a, b = make_account(allow_overdraft=True), make_account()

    with pytest.raises(errors.CurrencyMismatch):
        post(
            client=api_client_record,
            kind=TransactionKind.JOURNAL,
            legs=[Leg(a.id, Decimal("1"), "EUR"), Leg(b.id, Decimal("-1"), "EUR")],
        )


@pytest.mark.parametrize(("currency", "amount"), [("USD", "0.001"), ("JPY", "1.5")])
def test_amounts_finer_than_the_currency_are_rejected(
    api_client_record: ApiClient, make_account: AccountFactory, currency: str, amount: str
) -> None:
    a, b = make_account(currency, allow_overdraft=True), make_account(currency)

    with pytest.raises(errors.InvalidAmount):
        post(
            client=api_client_record,
            kind=TransactionKind.JOURNAL,
            legs=[Leg(a.id, Decimal(amount), currency), Leg(b.id, -Decimal(amount), currency)],
        )


def test_another_clients_account_is_not_found(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    other = ApiClient.objects.create(name="other", key_hash="1" * 64)
    theirs = accounts.create_account(
        client=other, owner_id="x", currency="USD", type=AccountType.LIABILITY
    )

    with pytest.raises(errors.NotFound):
        post(
            client=api_client_record,
            kind=TransactionKind.JOURNAL,
            legs=[
                Leg(make_account(allow_overdraft=True).id, Decimal("1"), "USD"),
                Leg(theirs.id, Decimal("-1"), "USD"),
            ],
        )


def test_overdraft_is_refused_without_permission(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet, other = make_account(), make_account()
    fund(wallet, "5")

    with pytest.raises(errors.InsufficientFunds):
        post(
            client=api_client_record,
            kind=TransactionKind.TRANSFER,
            legs=[Leg(wallet.id, Decimal("6"), "USD"), Leg(other.id, Decimal("-6"), "USD")],
        )
    assert balance_of(wallet) == Decimal("5")


def test_frozen_account_can_receive_but_not_send(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet, other = make_account(), make_account()
    fund(wallet, "10")
    fund(other, "10")
    accounts.set_status(api_client_record, wallet.id, AccountStatus.FROZEN)

    post(
        client=api_client_record,
        kind=TransactionKind.TRANSFER,
        legs=[Leg(other.id, Decimal("1"), "USD"), Leg(wallet.id, Decimal("-1"), "USD")],
    )
    with pytest.raises(errors.AccountInactive):
        post(
            client=api_client_record,
            kind=TransactionKind.TRANSFER,
            legs=[Leg(wallet.id, Decimal("1"), "USD"), Leg(other.id, Decimal("-1"), "USD")],
        )


def test_snapshots_are_taken_as_entries_accumulate(make_account: AccountFactory) -> None:
    wallet = make_account()
    for _ in range(12):  # LEDGER_SNAPSHOT_EVERY is 5 in test settings
        fund(wallet, "1.25")

    snapshots = list(BalanceSnapshot.objects.filter(account=wallet).order_by("as_of_entry_id"))
    assert len(snapshots) == 2
    assert snapshots[-1].balance == Decimal("-12.50")  # raw sum of a credit-normal account
    assert balance_of(wallet) == Decimal("15.00")


def test_balance_with_snapshots_equals_the_plain_sum(make_account: AccountFactory) -> None:
    wallet = make_account()
    for i in range(1, 14):
        fund(wallet, str(i))

    plain = sum(e.amount for e in Entry.objects.filter(account=wallet)) * wallet.normal_sign
    assert balances.get_balance(wallet).ledger == plain == Decimal(91)


def test_take_snapshot_is_a_no_op_when_nothing_changed(make_account: AccountFactory) -> None:
    wallet = make_account()
    fund(wallet, "3")

    first = balances.take_snapshot(wallet)

    assert first is not None
    assert first.balance == Decimal("-3")
    assert balances.take_snapshot(wallet) is None
