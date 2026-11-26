from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import HttpRequest
from ninja import Field, Query, Router, Schema, Status

from ledger.api import pagination, presenters
from ledger.api.auth import client_of
from ledger.api.idempotency import OPENAPI_EXTRA, idempotent
from ledger.api.schemas import CaptureIn, HoldIn, HoldOut, HoldPage
from ledger.models import HoldStatus
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


class HoldQuery(Schema):
    account_id: UUID | None = None
    status: HoldStatus | None = Field(
        None, description="Effective status: overdue holds are expired."
    )
    cursor: str | None = None
    limit: int = Field(50, ge=1, le=200)


@router.get("/holds", response=HoldPage)
def list_holds(request: HttpRequest, query: Query[HoldQuery]) -> dict[str, Any]:
    """Holds newest first, paged by an opaque cursor."""
    rows = holds.list_holds(client_of(request), account_id=query.account_id, status=query.status)
    page, next_cursor = pagination.page_newest_first(rows, query.cursor, query.limit)
    return {"items": [presenters.hold(h) for h in page], "next_cursor": next_cursor}


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
