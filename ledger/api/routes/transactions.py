from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from ledger import errors
from ledger.api import presenters
from ledger.api.auth import client_of
from ledger.api.idempotency import OPENAPI_EXTRA, idempotent
from ledger.api.schemas import (
    DepositIn,
    JournalIn,
    ReverseIn,
    TransactionOut,
    TransferIn,
    WithdrawalIn,
)
from ledger.models import Transaction
from ledger.services import payments, reversals
from ledger.services.posting import Leg

router = Router(tags=["transactions"])

Created = Status[dict[str, Any]]


@router.post("/deposits", response={201: TransactionOut}, openapi_extra=OPENAPI_EXTRA)
@idempotent
def deposit(request: HttpRequest, payload: DepositIn) -> Created:
    txn = payments.deposit(
        client=client_of(request),
        account_id=payload.account_id,
        amount=Decimal(payload.amount),
        currency=payload.currency,
        description=payload.description,
        metadata=payload.metadata,
    )
    return Status(201, presenters.transaction(txn))


@router.post("/withdrawals", response={201: TransactionOut}, openapi_extra=OPENAPI_EXTRA)
@idempotent
def withdraw(request: HttpRequest, payload: WithdrawalIn) -> Created:
    txn = payments.withdraw(
        client=client_of(request),
        account_id=payload.account_id,
        amount=Decimal(payload.amount),
        currency=payload.currency,
        description=payload.description,
        metadata=payload.metadata,
    )
    return Status(201, presenters.transaction(txn))


@router.post("/transfers", response={201: TransactionOut}, openapi_extra=OPENAPI_EXTRA)
@idempotent
def transfer(request: HttpRequest, payload: TransferIn) -> Created:
    txn = payments.transfer(
        client=client_of(request),
        source_id=payload.source,
        destination_id=payload.destination,
        amount=Decimal(payload.amount),
        currency=payload.currency,
        description=payload.description,
        metadata=payload.metadata,
    )
    return Status(201, presenters.transaction(txn))


@router.post("/transactions", response={201: TransactionOut}, openapi_extra=OPENAPI_EXTRA)
@idempotent
def journal(request: HttpRequest, payload: JournalIn) -> Created:
    """Post any balanced set of entries, e.g. a trade with a fee leg."""
    txn = payments.journal(
        client=client_of(request),
        legs=[Leg(e.account_id, Decimal(e.amount), e.currency) for e in payload.entries],
        description=payload.description,
        metadata=payload.metadata,
    )
    return Status(201, presenters.transaction(txn))


@router.get("/transactions/{transaction_id}", response=TransactionOut)
def get_transaction(request: HttpRequest, transaction_id: UUID) -> dict[str, Any]:
    try:
        txn = Transaction.objects.get(id=transaction_id, client=client_of(request))
    except Transaction.DoesNotExist:
        raise errors.NotFound(f"transaction {transaction_id} does not exist") from None
    return presenters.transaction(txn)


@router.post(
    "/transactions/{transaction_id}/reverse",
    response={201: TransactionOut},
    openapi_extra=OPENAPI_EXTRA,
)
@idempotent
def reverse(request: HttpRequest, transaction_id: UUID, payload: ReverseIn) -> Created:
    txn = reversals.reverse(
        client=client_of(request),
        transaction_id=transaction_id,
        description=payload.description,
    )
    return Status(201, presenters.transaction(txn))
