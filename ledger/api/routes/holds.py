from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from ledger.api import presenters
from ledger.api.auth import client_of
from ledger.api.idempotency import OPENAPI_EXTRA, idempotent
from ledger.api.schemas import CaptureIn, HoldIn, HoldOut
from ledger.services import holds

router = Router(tags=["holds"])


@router.post("/holds", response={201: HoldOut}, openapi_extra=OPENAPI_EXTRA)
@idempotent
def create_hold(request: HttpRequest, payload: HoldIn) -> Status[dict[str, Any]]:
    hold = holds.create_hold(
        client=client_of(request),
        account_id=payload.account_id,
        amount=Decimal(payload.amount),
        currency=payload.currency,
        reason=payload.reason,
        expires_at=payload.expires_at,
    )
    return Status(201, presenters.hold(hold))


@router.get("/holds/{hold_id}", response=HoldOut)
def get_hold(request: HttpRequest, hold_id: UUID) -> dict[str, Any]:
    return presenters.hold(holds.get_hold(client_of(request), hold_id))


@router.post("/holds/{hold_id}/capture", response={200: HoldOut}, openapi_extra=OPENAPI_EXTRA)
@idempotent
def capture_hold(request: HttpRequest, hold_id: UUID, payload: CaptureIn) -> Status[dict[str, Any]]:
    hold = holds.capture_hold(
        client=client_of(request),
        hold_id=hold_id,
        destination_id=payload.to,
        amount=Decimal(payload.amount) if payload.amount is not None else None,
        description=payload.description,
    )
    return Status(200, presenters.hold(hold))


@router.post("/holds/{hold_id}/release", response={200: HoldOut}, openapi_extra=OPENAPI_EXTRA)
@idempotent
def release_hold(request: HttpRequest, hold_id: UUID) -> Status[dict[str, Any]]:
    hold = holds.release_hold(client=client_of(request), hold_id=hold_id)
    return Status(200, presenters.hold(hold))
