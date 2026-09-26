from decimal import Decimal

import pytest

from ledger import errors
from ledger.models import Account, AccountType, ApiClient, TransactionKind
from ledger.services import payments
from ledger.services.posting import Leg
from tests.conftest import AccountFactory, balance_of, fund


def test_deposit_credits_the_account_and_debits_settlement(make_account: AccountFactory) -> None:
    wallet = make_account()

    fund(wallet, "12.50")

    assert balance_of(wallet) == Decimal("12.50")
    settlement = Account.objects.get(is_settlement=True, currency="USD")
    assert settlement.type == AccountType.ASSET
    assert balance_of(settlement) == Decimal("12.50")


def test_withdrawal_reduces_both_sides(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet = make_account()
    fund(wallet, "20")

    txn = payments.withdraw(
        client=api_client_record, account_id=wallet.id, amount=Decimal("7.5"), currency="USD"
    )

    assert txn.kind == TransactionKind.WITHDRAWAL
    assert balance_of(wallet) == Decimal("12.5")
    assert balance_of(Account.objects.get(is_settlement=True)) == Decimal("12.5")


def test_withdrawal_beyond_balance_is_refused(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet = make_account()
    fund(wallet, "1")

    with pytest.raises(errors.InsufficientFunds):
        payments.withdraw(
            client=api_client_record, account_id=wallet.id, amount=Decimal("1.01"), currency="USD"
        )


@pytest.mark.parametrize("amount", ["0", "-1"])
def test_non_positive_amounts_are_refused(
    api_client_record: ApiClient, make_account: AccountFactory, amount: str
) -> None:
    with pytest.raises(errors.InvalidAmount):
        payments.deposit(
            client=api_client_record,
            account_id=make_account().id,
            amount=Decimal(amount),
            currency="USD",
        )


def test_deposit_in_the_wrong_currency_is_refused(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    with pytest.raises(errors.CurrencyMismatch):
        payments.deposit(
            client=api_client_record,
            account_id=make_account().id,
            amount=Decimal(1),
            currency="EUR",
        )


def test_deposit_into_an_asset_account_is_refused(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    with pytest.raises(errors.IncompatibleAccounts):
        payments.deposit(
            client=api_client_record,
            account_id=make_account(kind=AccountType.ASSET).id,
            amount=Decimal(1),
            currency="USD",
        )


def test_transfer_moves_value_between_wallets(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    alice, bob = make_account(), make_account()
    fund(alice, "10")

    payments.transfer(
        client=api_client_record,
        source_id=alice.id,
        destination_id=bob.id,
        amount=Decimal("4"),
        currency="USD",
    )

    assert balance_of(alice) == Decimal("6")
    assert balance_of(bob) == Decimal("4")


def test_transfer_between_debit_normal_accounts(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    bank_a = make_account(kind=AccountType.ASSET, allow_overdraft=True)
    bank_b = make_account(kind=AccountType.ASSET)

    payments.transfer(
        client=api_client_record,
        source_id=bank_a.id,
        destination_id=bank_b.id,
        amount=Decimal("3"),
        currency="USD",
    )

    assert balance_of(bank_a) == Decimal("-3")
    assert balance_of(bank_b) == Decimal("3")


def test_transfer_across_ledger_sides_is_refused(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    with pytest.raises(errors.IncompatibleAccounts):
        payments.transfer(
            client=api_client_record,
            source_id=make_account().id,
            destination_id=make_account(kind=AccountType.ASSET).id,
            amount=Decimal(1),
            currency="USD",
        )


def test_transfer_to_self_is_refused(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet = make_account()
    with pytest.raises(errors.IncompatibleAccounts):
        payments.transfer(
            client=api_client_record,
            source_id=wallet.id,
            destination_id=wallet.id,
            amount=Decimal(1),
            currency="USD",
        )


def test_journal_posts_a_trade_with_a_fee_leg(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    buyer, seller = make_account(), make_account()
    fees = make_account(kind=AccountType.REVENUE)
    fund(buyer, "100")

    payments.journal(
        client=api_client_record,
        legs=[
            Leg(buyer.id, Decimal("100"), "USD"),
            Leg(seller.id, Decimal("-99"), "USD"),
            Leg(fees.id, Decimal("-1"), "USD"),
        ],
    )

    assert balance_of(buyer) == 0
    assert balance_of(seller) == Decimal("99")
    assert balance_of(fees) == Decimal("1")
