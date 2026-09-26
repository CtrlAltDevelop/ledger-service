"""Deposits, withdrawals, transfers and free-form journal entries.

Each is a thin translation into legs for :func:`ledger.services.posting.post`.

Deposits and withdrawals move money across the ledger's edge, so they pair a
customer account with the client's settlement account. They only make sense
for credit-normal accounts (liabilities, equity, revenue): money a customer
deposits is money the business owes them.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from ledger import errors
from ledger.models import Account, ApiClient, Transaction, TransactionKind
from ledger.services import accounts
from ledger.services.posting import Leg, post, require_positive


def deposit(
    *,
    client: ApiClient,
    account_id: UUID,
    amount: Decimal,
    currency: str,
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> Transaction:
    account = _customer_account(client, account_id, currency)
    require_positive(amount)
    settlement = accounts.settlement_account(client, account.currency)
    return post(
        client=client,
        kind=TransactionKind.DEPOSIT,
        legs=[
            Leg(settlement.id, amount, currency),
            Leg(account.id, -amount, currency),
        ],
        description=description,
        metadata=metadata,
    )


def withdraw(
    *,
    client: ApiClient,
    account_id: UUID,
    amount: Decimal,
    currency: str,
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> Transaction:
    account = _customer_account(client, account_id, currency)
    require_positive(amount)
    settlement = accounts.settlement_account(client, account.currency)
    return post(
        client=client,
        kind=TransactionKind.WITHDRAWAL,
        legs=[
            Leg(account.id, amount, currency),
            Leg(settlement.id, -amount, currency),
        ],
        description=description,
        metadata=metadata,
    )


def transfer_legs(source: Account, destination: Account, amount: Decimal) -> list[Leg]:
    """Legs that lower ``source``'s balance and raise ``destination``'s by ``amount``.

    Both accounts must share a normal side; moving value between, say, an
    asset and a liability raises or lowers both, which is a journal entry,
    not a transfer.
    """
    if source.id == destination.id:
        raise errors.IncompatibleAccounts("cannot transfer from an account to itself")
    if source.currency_id != destination.currency_id:
        raise errors.CurrencyMismatch(
            f"cannot transfer {source.currency_id} into a {destination.currency_id} account"
        )
    if source.normal_sign != destination.normal_sign:
        raise errors.IncompatibleAccounts(
            f"a transfer needs accounts on the same side of the ledger; "
            f"{source.type} and {destination.type} are not. Post a journal entry instead."
        )
    sign = source.normal_sign
    currency = source.currency_id
    return [
        Leg(source.id, -amount * sign, currency),
        Leg(destination.id, amount * sign, currency),
    ]


def transfer(
    *,
    client: ApiClient,
    source_id: UUID,
    destination_id: UUID,
    amount: Decimal,
    currency: str,
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> Transaction:
    source = accounts.get_account(client, source_id)
    destination = accounts.get_account(client, destination_id)
    accounts.require_currency(source, currency)
    require_positive(amount)
    return post(
        client=client,
        kind=TransactionKind.TRANSFER,
        legs=transfer_legs(source, destination, amount),
        description=description,
        metadata=metadata,
    )


def journal(
    *,
    client: ApiClient,
    legs: list[Leg],
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> Transaction:
    """Post arbitrary balanced legs, e.g. a trade with a fee leg."""
    return post(
        client=client,
        kind=TransactionKind.JOURNAL,
        legs=legs,
        description=description,
        metadata=metadata,
    )


def _customer_account(client: ApiClient, account_id: UUID, currency: str) -> Account:
    account = accounts.get_account(client, account_id)
    accounts.require_currency(account, currency)
    if account.normal_sign != -1 or account.is_settlement:
        raise errors.IncompatibleAccounts(
            f"deposits and withdrawals apply to credit-normal accounts, not {account.type}"
        )
    return account
