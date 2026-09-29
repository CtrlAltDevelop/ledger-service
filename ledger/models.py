"""The ledger's tables.

Balances are not stored anywhere on ``Account``. An account's balance is the
sum of its entries, optionally starting from a ``BalanceSnapshot``; see
``ledger.services.balances``.

``Transaction`` and ``Entry`` are append-only. Migration 0002 installs
triggers that reject UPDATE and DELETE on both tables, so a mistake is
corrected by posting a reversal, never by editing history.
"""

import uuid
from typing import ClassVar

from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from ledger.money import DECIMAL_PLACES, MAX_DIGITS


class ApiClient(models.Model):
    """A caller of the API. Accounts and idempotency keys are scoped to one."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    # SHA-256 of the bearer token; the token itself is shown once and not kept.
    key_hash = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Currency(models.Model):
    # ISO 4217 for fiat; crypto tickers such as USDT run longer than three.
    code = models.CharField(max_length=8, primary_key=True)
    # Minor-unit digits: 2 for USD, 0 for JPY, 8 for BTC.
    scale = models.PositiveSmallIntegerField()

    class Meta:
        verbose_name_plural = "currencies"
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=Q(scale__lte=DECIMAL_PLACES), name="currency_scale_fits_storage"
            ),
        ]

    def __str__(self) -> str:
        return self.code


class AccountType(models.TextChoices):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


DEBIT_NORMAL_TYPES = frozenset({AccountType.ASSET, AccountType.EXPENSE})


class AccountStatus(models.TextChoices):
    ACTIVE = "active"
    FROZEN = "frozen"


class Account(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(ApiClient, on_delete=models.PROTECT, related_name="accounts")
    owner_id = models.CharField(max_length=100)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, db_column="currency")
    type = models.CharField(max_length=16, choices=AccountType.choices)
    status = models.CharField(
        max_length=16, choices=AccountStatus.choices, default=AccountStatus.ACTIVE
    )
    # Settlement accounts mirror money outside the ledger and may go negative.
    allow_overdraft = models.BooleanField(default=False)
    # Set on the per-currency settlement account the service creates itself.
    is_settlement = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes: ClassVar = [models.Index(fields=["client", "owner_id"])]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["client", "currency"],
                condition=Q(is_settlement=True),
                name="one_settlement_account_per_client_currency",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.id} ({self.currency_id} {self.type})"

    @property
    def normal_sign(self) -> int:
        """+1 if a debit raises this account's balance, -1 if a credit does.

        Entry amounts are signed: positive is a debit, negative a credit. The
        balance shown to a client is ``sum(amount) * normal_sign``.
        """
        return 1 if self.type in DEBIT_NORMAL_TYPES else -1


class TransactionKind(models.TextChoices):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"
    JOURNAL = "journal"
    HOLD_CAPTURE = "hold_capture"
    REVERSAL = "reversal"


class Transaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(ApiClient, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=16, choices=TransactionKind.choices)
    description = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    # Unique, so a transaction can be reversed at most once.
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.kind} {self.id}"


class Entry(models.Model):
    """One leg of a transaction. Positive amounts are debits, negative credits."""

    transaction = models.ForeignKey(Transaction, on_delete=models.PROTECT, related_name="entries")
    # Indexed through (account, id) below; a lone account index would be redundant.
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="entries", db_index=False
    )
    amount = models.DecimalField(max_digits=MAX_DIGITS, decimal_places=DECIMAL_PLACES)
    # Copied from the account so per-currency sums need no join.
    currency = models.CharField(max_length=8)
    # Set to the transaction's timestamp, so every leg of a transaction falls
    # on the same side of any as-of cut-off.
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "entries"
        indexes: ClassVar = [
            models.Index(fields=["account", "id"]),
            models.Index(fields=["account", "created_at"]),
        ]
        constraints: ClassVar = [
            models.CheckConstraint(condition=~Q(amount=0), name="entry_amount_nonzero"),
        ]

    def __str__(self) -> str:
        return f"{self.account_id} {self.amount} {self.currency}"


class HoldStatus(models.TextChoices):
    ACTIVE = "active"
    CAPTURED = "captured"
    RELEASED = "released"


class Hold(models.Model):
    """Funds reserved on an account, e.g. for an open order.

    A hold is not an entry: it moves no money, it only lowers the available
    balance until it is captured (money moves), released, or expires.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="holds")
    amount = models.DecimalField(max_digits=MAX_DIGITS, decimal_places=DECIMAL_PLACES)
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=HoldStatus.choices, default=HoldStatus.ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True)
    capture_transaction = models.OneToOneField(
        Transaction, null=True, blank=True, on_delete=models.PROTECT, related_name="captured_hold"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes: ClassVar = [
            models.Index(
                fields=["account"], condition=Q(status="active"), name="hold_active_by_account"
            ),
        ]
        constraints: ClassVar = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="hold_amount_positive"),
            models.CheckConstraint(
                condition=Q(status="captured", capture_transaction__isnull=False)
                | (~Q(status="captured") & Q(capture_transaction__isnull=True)),
                name="hold_captured_iff_transaction",
            ),
        ]

    def __str__(self) -> str:
        return f"hold {self.id} {self.amount} ({self.status})"


class BalanceSnapshot(models.Model):
    """The sum of an account's entries up to and including ``as_of_entry_id``.

    Taken while the account row is locked, so no entry with a smaller id can
    commit afterwards; see docs/adr/0003.
    """

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="snapshots")
    as_of_entry_id = models.BigIntegerField()
    # Raw signed sum of entry amounts, not multiplied by the normal sign.
    balance = models.DecimalField(max_digits=MAX_DIGITS, decimal_places=DECIMAL_PLACES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["account", "as_of_entry_id"], name="one_snapshot_per_account_entry"
            ),
        ]
        indexes: ClassVar = [
            models.Index(F("account"), F("as_of_entry_id").desc(), name="snapshot_latest"),
        ]

    def __str__(self) -> str:
        return f"{self.account_id} @ {self.as_of_entry_id}"


class IdempotencyRecord(models.Model):
    """The stored outcome of a write, keyed by client and Idempotency-Key."""

    client = models.ForeignKey(ApiClient, on_delete=models.PROTECT)
    key = models.CharField(max_length=255)
    # SHA-256 over method, path and canonical body.
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(null=True)
    # The exact bytes sent the first time, as text. Not jsonb: jsonb reorders
    # keys, and a replay should be byte-for-byte the original response.
    response_body = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["client", "key"], name="idempotency_key_per_client"),
        ]

    def __str__(self) -> str:
        return f"{self.client_id}:{self.key}"


class OutboxEvent(models.Model):
    """An event written in the same transaction as the change it describes.

    ``outbox_worker`` publishes it afterwards and stamps ``published_at``.
    Delivery is at least once: consumers deduplicate on ``id``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    aggregate_type = models.CharField(max_length=32)
    aggregate_id = models.UUIDField()
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        indexes: ClassVar = [
            models.Index(
                fields=["created_at"],
                condition=Q(published_at__isnull=True),
                name="outbox_unpublished",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.aggregate_id}"
