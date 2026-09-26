"""Request and response bodies.

Amounts cross the wire as decimal strings ("12.50"), never JSON numbers,
which most clients would parse into a float.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from ninja import Field, Schema

from ledger.models import AccountStatus, AccountType, TransactionKind

Amount = Annotated[
    str,
    Field(pattern=r"^\d{1,20}(\.\d{1,18})?$", examples=["12.50"], description="Positive decimal."),
]
SignedAmount = Annotated[
    str,
    Field(
        pattern=r"^-?\d{1,20}(\.\d{1,18})?$",
        examples=["-12.50"],
        description="Positive for a debit, negative for a credit.",
    ),
]
CurrencyCode = Annotated[str, Field(min_length=3, max_length=8, examples=["USD"])]
Metadata = Annotated[dict[str, Any], Field(default_factory=dict)]
Description = Annotated[str, Field(default="", max_length=255)]


# --- Accounts ----------------------------------------------------------------


class AccountIn(Schema):
    owner_id: str = Field(min_length=1, max_length=100)
    currency: CurrencyCode
    type: AccountType = AccountType.LIABILITY
    allow_overdraft: bool = False


class AccountPatch(Schema):
    status: AccountStatus


class AccountOut(Schema):
    id: UUID
    owner_id: str
    currency: str
    type: AccountType
    status: AccountStatus
    allow_overdraft: bool
    created_at: datetime


class BalanceOut(Schema):
    account_id: UUID
    currency: str
    ledger: str = Field(description="Sum of posted entries, in the account's normal direction.")
    held: str | None = Field(description="Active holds. Omitted for historical balances.")
    available: str | None = Field(description="ledger - held. Omitted for historical balances.")
    as_of: datetime | None


# --- Transactions ------------------------------------------------------------


class DepositIn(Schema):
    account_id: UUID
    amount: Amount
    currency: CurrencyCode
    description: Description
    metadata: Metadata


WithdrawalIn = DepositIn


class TransferIn(Schema):
    source: UUID = Field(alias="from")
    destination: UUID = Field(alias="to")
    amount: Amount
    currency: CurrencyCode
    description: Description
    metadata: Metadata


class LegIn(Schema):
    account_id: UUID
    amount: SignedAmount
    currency: CurrencyCode


class JournalIn(Schema):
    entries: list[LegIn] = Field(min_length=2, max_length=50)
    description: Description
    metadata: Metadata


class ReverseIn(Schema):
    description: Description


class EntryOut(Schema):
    id: int
    transaction_id: UUID
    account_id: UUID
    amount: str
    currency: str
    created_at: datetime


class TransactionOut(Schema):
    id: UUID
    kind: TransactionKind
    description: str
    metadata: dict[str, Any]
    reverses: UUID | None
    reversed_by: UUID | None
    created_at: datetime
    entries: list[EntryOut]


class EntryPage(Schema):
    items: list[EntryOut]
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next page.")
