"""Balances, derived from entries and never stored on the account.

A balance is the latest snapshot for the account plus every entry after it.
Snapshots are only an optimisation: dropping every one of them changes no
balance, it only makes reads slower. See docs/adr/0003.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from ledger.models import Account, BalanceSnapshot, Entry, Hold, HoldStatus

_RAW_BALANCES_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (account_id) account_id, as_of_entry_id, balance
      FROM ledger_balancesnapshot
     WHERE account_id = ANY(%(ids)s)
     ORDER BY account_id, as_of_entry_id DESC
)
SELECT a.id,
       COALESCE(s.balance, 0) + COALESCE(SUM(e.amount), 0),
       COUNT(e.id),
       COALESCE(MAX(e.id), s.as_of_entry_id, 0)
  FROM ledger_account a
  LEFT JOIN latest s ON s.account_id = a.id
  LEFT JOIN ledger_entry e
         ON e.account_id = a.id AND e.id > COALESCE(s.as_of_entry_id, 0)
 WHERE a.id = ANY(%(ids)s)
 GROUP BY a.id, s.balance, s.as_of_entry_id
"""


@dataclass(frozen=True, slots=True)
class RawBalance:
    """The signed sum of an account's entries: debits positive, credits negative."""

    total: Decimal
    entries_since_snapshot: int
    last_entry_id: int


@dataclass(frozen=True, slots=True)
class Balance:
    """An account's balance in its normal direction, as a client sees it."""

    ledger: Decimal
    held: Decimal

    @property
    def available(self) -> Decimal:
        return self.ledger - self.held


def raw_balances(account_ids: Iterable[UUID]) -> dict[UUID, RawBalance]:
    ids = list(account_ids)
    with connection.cursor() as cursor:
        cursor.execute(_RAW_BALANCES_SQL, {"ids": ids})
        return {
            row[0]: RawBalance(total=row[1], entries_since_snapshot=row[2], last_entry_id=row[3])
            for row in cursor.fetchall()
        }


def held_amounts(
    account_ids: Iterable[UUID], *, exclude_hold: UUID | None = None
) -> dict[UUID, Decimal]:
    """Sum of active, unexpired holds per account."""
    holds = Hold.objects.filter(account_id__in=list(account_ids), status=HoldStatus.ACTIVE).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    )
    if exclude_hold is not None:
        holds = holds.exclude(id=exclude_hold)
    rows = holds.values("account_id").annotate(total=Sum("amount"))
    return {row["account_id"]: row["total"] for row in rows}


def get_balance(account: Account) -> Balance:
    raw = raw_balances([account.id])[account.id]
    held = held_amounts([account.id]).get(account.id, Decimal(0))
    return Balance(ledger=raw.total * account.normal_sign, held=held)


def balance_as_of(account: Account, as_of: datetime) -> Decimal:
    """The ledger balance as recorded at ``as_of``, summed from entries alone."""
    total = Entry.objects.filter(account=account, created_at__lte=as_of).aggregate(
        total=Sum("amount")
    )["total"]
    return (total or Decimal(0)) * account.normal_sign


def take_snapshot(account: Account, *, min_entries: int = 1) -> BalanceSnapshot | None:
    """Snapshot one account's balance, or return None if fewer than
    ``min_entries`` entries were posted since the last snapshot.

    Locks the account row first. Postings lock it too before inserting
    entries, so once the lock is held no entry with a smaller id can still be
    in flight, and the snapshot can never miss one.
    """
    with transaction.atomic():
        Account.objects.select_for_update().filter(pk=account.pk).get()
        raw = raw_balances([account.id])[account.id]
        if raw.entries_since_snapshot < max(min_entries, 1):
            return None
        return BalanceSnapshot.objects.create(
            account=account, as_of_entry_id=raw.last_entry_id, balance=raw.total
        )
