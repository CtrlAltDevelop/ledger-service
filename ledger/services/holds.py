"""Holds: reserve funds now, move them later or let them go.

An exchange places a hold when an order opens, captures it when the order
fills and releases it when the order is cancelled. While active, a hold
lowers the account's available balance but moves no money.

An expired hold stops counting straight away, with no job needed to sweep
it; it stays ``active`` in the table and reads as ``expired``.

Lock order matches posting: accounts (by id) first, then the hold row.
Release takes only the hold row, and holds no account lock while waiting
for it, so the two paths cannot deadlock.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from ledger import errors, money
from ledger.models import AccountStatus, ApiClient, Hold, HoldStatus, TransactionKind
from ledger.services import accounts, balances, outbox
from ledger.services.payments import transfer_legs
from ledger.services.posting import lock_accounts, post, require_positive


def get_hold(client: ApiClient, hold_id: UUID, *, for_update: bool = False) -> Hold:
    holds = Hold.objects.select_related("account__currency")
    if for_update:
        holds = holds.select_for_update(of=("self",))
    try:
        return holds.get(id=hold_id, account__client=client)
    except Hold.DoesNotExist:
        raise errors.NotFound(f"hold {hold_id} does not exist") from None


def effective_status(hold: Hold) -> str:
    if hold.status == HoldStatus.ACTIVE and is_expired(hold):
        return "expired"
    return hold.status


def is_expired(hold: Hold) -> bool:
    return hold.expires_at is not None and hold.expires_at <= timezone.now()


def create_hold(
    *,
    client: ApiClient,
    account_id: UUID,
    amount: Decimal,
    currency: str,
    reason: str = "",
    expires_at: datetime | None = None,
) -> Hold:
    require_positive(amount)
    if expires_at is not None and expires_at <= timezone.now():
        raise errors.InvalidExpiry(f"expires_at {expires_at.isoformat()} is not in the future")
    with transaction.atomic():
        account = lock_accounts(client, [account_id])[account_id]
        accounts.require_currency(account, currency)
        if not money.fits_scale(amount, account.currency.scale):
            raise errors.InvalidAmount(f"{amount} has more precision than {currency} allows")
        if account.status != AccountStatus.ACTIVE:
            raise errors.AccountInactive(f"account {account.id} is {account.status}")

        balance = balances.get_balance(account)
        if not account.allow_overdraft and balance.available < amount:
            raise errors.InsufficientFunds(
                f"account {account.id} has insufficient available funds",
                account_id=str(account.id),
            )
        hold = Hold.objects.create(
            account=account, amount=amount, reason=reason, expires_at=expires_at
        )
        _announce(hold, "hold.created")
    return hold


def capture_hold(
    *,
    client: ApiClient,
    hold_id: UUID,
    destination_id: UUID,
    amount: Decimal | None = None,
    description: str = "",
) -> Hold:
    """Move up to the held amount to ``destination``; release the remainder."""
    with transaction.atomic():
        hold = get_hold(client, hold_id)
        destination = accounts.get_account(client, destination_id)
        lock_accounts(client, [hold.account_id, destination.id])
        hold = get_hold(client, hold_id, for_update=True)
        _require_active(hold)

        amount = hold.amount if amount is None else amount
        require_positive(amount)
        if amount > hold.amount:
            raise errors.InvalidAmount(f"cannot capture {amount}; the hold is for {hold.amount}")

        txn = post(
            client=client,
            kind=TransactionKind.HOLD_CAPTURE,
            legs=transfer_legs(hold.account, destination, amount),
            description=description,
            metadata={"hold_id": str(hold.id)},
            consuming_hold=hold.id,
        )
        hold.status = HoldStatus.CAPTURED
        hold.capture_transaction = txn
        hold.resolved_at = timezone.now()
        hold.save(update_fields=["status", "capture_transaction", "resolved_at"])
        _announce(hold, "hold.captured", captured=f"{amount:f}")
    return hold


def release_hold(*, client: ApiClient, hold_id: UUID) -> Hold:
    with transaction.atomic():
        hold = get_hold(client, hold_id, for_update=True)
        _require_unresolved(hold)
        # Releasing an expired hold is allowed: it only makes the state explicit.
        hold.status = HoldStatus.RELEASED
        hold.resolved_at = timezone.now()
        hold.save(update_fields=["status", "resolved_at"])
        _announce(hold, "hold.released")
    return hold


def _require_unresolved(hold: Hold) -> None:
    if hold.status != HoldStatus.ACTIVE:
        raise errors.HoldNotActive(f"hold {hold.id} is already {hold.status}")


def _require_active(hold: Hold) -> None:
    _require_unresolved(hold)
    if hold.expires_at is not None and is_expired(hold):
        raise errors.HoldNotActive(f"hold {hold.id} expired at {hold.expires_at.isoformat()}")


def _announce(hold: Hold, event_type: str, **extra: str) -> None:
    outbox.enqueue(
        event_type,
        aggregate_type="hold",
        aggregate_id=hold.id,
        payload={
            "hold_id": str(hold.id),
            "account_id": str(hold.account_id),
            "amount": f"{hold.amount:f}",
            "status": hold.status,
            **extra,
        },
    )
