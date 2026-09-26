"""Posting: the one place entries are written.

Every movement of money, from a deposit to a hold capture to a reversal, is
turned into a list of legs and handed to :func:`post`, which

1. locks every account involved, in ascending id order so two postings that
   share accounts always queue instead of deadlocking;
2. checks the legs balance per currency and fit each currency's precision;
3. checks no account that may not overdraw would end below zero, counting
   active holds against it;
4. writes the transaction, its entries, any snapshot that falls due, and a
   ``transaction.posted`` outbox event, all in one database transaction.

See docs/adr/0002 for why the locks are pessimistic.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction

from ledger import errors, money
from ledger.models import (
    Account,
    AccountStatus,
    ApiClient,
    BalanceSnapshot,
    Entry,
    Transaction,
    TransactionKind,
)
from ledger.services import balances, outbox


@dataclass(frozen=True, slots=True)
class Leg:
    """One side of a posting. Positive ``amount`` is a debit, negative a credit."""

    account_id: UUID
    amount: Decimal
    currency: str


def require_positive(amount: Decimal) -> None:
    if not money.fits_storage(amount) or amount <= 0:
        raise errors.InvalidAmount(f"amount must be a positive number, got {amount}")


def lock_accounts(client: ApiClient, account_ids: Iterable[UUID]) -> dict[UUID, Account]:
    """Lock the client's accounts, in id order, until the transaction ends."""
    wanted = set(account_ids)
    locked = (
        Account.objects.select_for_update(of=("self",))
        .select_related("currency")
        .filter(client=client, id__in=wanted)
        .order_by("id")
    )
    accounts = {account.id: account for account in locked}
    missing = wanted - accounts.keys()
    if missing:
        raise errors.NotFound(f"account {min(missing)} does not exist")
    return accounts


def post(
    *,
    client: ApiClient,
    kind: TransactionKind,
    legs: Sequence[Leg],
    description: str = "",
    metadata: dict[str, Any] | None = None,
    reverses: Transaction | None = None,
    consuming_hold: UUID | None = None,
) -> Transaction:
    """Post a balanced transaction atomically, or raise and write nothing.

    ``consuming_hold`` names a hold this posting captures; its amount stops
    counting against the account's available balance for this check.
    """
    if len(legs) < 2:
        raise errors.UnbalancedTransaction("a transaction needs at least two entries")

    with transaction.atomic():
        accounts = lock_accounts(client, (leg.account_id for leg in legs))
        _validate_legs(legs, accounts)

        deltas: dict[UUID, Decimal] = defaultdict(Decimal)
        for leg in legs:
            deltas[leg.account_id] += leg.amount

        before = balances.raw_balances(accounts)
        _require_funds(accounts, deltas, before, consuming_hold)

        txn = Transaction.objects.create(
            client=client,
            kind=kind,
            description=description,
            metadata=metadata or {},
            reverses=reverses,
        )
        entries = Entry.objects.bulk_create(
            Entry(
                transaction=txn,
                account_id=leg.account_id,
                amount=leg.amount,
                currency=leg.currency,
            )
            for leg in legs
        )
        _snapshot_if_due(entries, before, deltas)
        outbox.enqueue(
            "transaction.posted",
            aggregate_type="transaction",
            aggregate_id=txn.id,
            payload=event_payload(txn, entries),
        )
    return txn


def event_payload(txn: Transaction, entries: Iterable[Entry]) -> dict[str, Any]:
    return {
        "transaction_id": str(txn.id),
        "kind": txn.kind,
        "reverses": str(txn.reverses_id) if txn.reverses_id else None,
        "created_at": txn.created_at.isoformat(),
        "entries": [
            {"account_id": str(e.account_id), "amount": f"{e.amount:f}", "currency": e.currency}
            for e in entries
        ],
    }


def _validate_legs(legs: Sequence[Leg], accounts: dict[UUID, Account]) -> None:
    per_currency: dict[str, Decimal] = defaultdict(Decimal)
    for leg in legs:
        account = accounts[leg.account_id]
        if leg.currency != account.currency_id:
            raise errors.CurrencyMismatch(
                f"account {account.id} holds {account.currency_id}, not {leg.currency}",
                account_id=str(account.id),
            )
        if leg.amount == 0 or not money.fits_storage(leg.amount):
            raise errors.InvalidAmount(f"entry amount {leg.amount} is out of range")
        if not money.fits_scale(leg.amount, account.currency.scale):
            raise errors.InvalidAmount(
                f"{leg.amount} has more precision than {leg.currency} allows "
                f"({account.currency.scale} decimal places)"
            )
        per_currency[leg.currency] += leg.amount

    unbalanced = {code: total for code, total in per_currency.items() if total != 0}
    if unbalanced:
        raise errors.UnbalancedTransaction(
            "entries must sum to zero in every currency",
            imbalance={code: f"{total:f}" for code, total in sorted(unbalanced.items())},
        )


def _require_funds(
    accounts: dict[UUID, Account],
    deltas: dict[UUID, Decimal],
    before: dict[UUID, balances.RawBalance],
    consuming_hold: UUID | None,
) -> None:
    # Only an account whose balance goes down can be overdrawn by this posting.
    shrinking = [
        account
        for account_id, account in accounts.items()
        if deltas[account_id] * account.normal_sign < 0
    ]
    for account in shrinking:
        if account.status != AccountStatus.ACTIVE:
            raise errors.AccountInactive(
                f"account {account.id} is {account.status} and cannot send funds",
                account_id=str(account.id),
            )

    guarded = [account for account in shrinking if not account.allow_overdraft]
    if not guarded:
        return
    held = balances.held_amounts((a.id for a in guarded), exclude_hold=consuming_hold)
    for account in guarded:
        after = (before[account.id].total + deltas[account.id]) * account.normal_sign
        available = after - held.get(account.id, Decimal(0))
        if available < 0:
            raise errors.InsufficientFunds(
                f"account {account.id} has insufficient available funds",
                account_id=str(account.id),
            )


def _snapshot_if_due(
    entries: Sequence[Entry],
    before: dict[UUID, balances.RawBalance],
    deltas: dict[UUID, Decimal],
) -> None:
    """Snapshot accounts that crossed the threshold, while still holding their locks."""
    last_entry: dict[UUID, int] = {}
    count: dict[UUID, int] = defaultdict(int)
    for entry in entries:
        # bulk_create fills in ids on Postgres.
        last_entry[entry.account_id] = max(entry.id, last_entry.get(entry.account_id, 0))
        count[entry.account_id] += 1

    due = [
        BalanceSnapshot(
            account_id=account_id,
            as_of_entry_id=last_entry[account_id],
            balance=before[account_id].total + deltas[account_id],
        )
        for account_id in last_entry
        if before[account_id].entries_since_snapshot + count[account_id]
        >= settings.LEDGER_SNAPSHOT_EVERY
    ]
    if due:
        BalanceSnapshot.objects.bulk_create(due)
