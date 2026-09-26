from datetime import datetime
from typing import Any
from uuid import UUID

from django.http import HttpRequest
from ninja import Field as NinjaField
from ninja import Query, Router, Schema, Status

from ledger.api import pagination, presenters
from ledger.api.auth import client_of
from ledger.api.idempotency import OPENAPI_EXTRA, idempotent
from ledger.api.schemas import AccountIn, AccountOut, AccountPatch, BalanceOut, EntryPage
from ledger.models import Entry
from ledger.services import accounts, balances

router = Router(tags=["accounts"])


@router.post("/accounts", response={201: AccountOut}, openapi_extra=OPENAPI_EXTRA)
@idempotent
def create_account(request: HttpRequest, payload: AccountIn) -> Status[dict[str, Any]]:
    account = accounts.create_account(
        client=client_of(request),
        owner_id=payload.owner_id,
        currency=payload.currency,
        type=payload.type,
        allow_overdraft=payload.allow_overdraft,
    )
    return Status(201, presenters.account(account))


@router.get("/accounts/{account_id}", response=AccountOut)
def get_account(request: HttpRequest, account_id: UUID) -> dict[str, Any]:
    return presenters.account(accounts.get_account(client_of(request), account_id))


@router.patch("/accounts/{account_id}", response=AccountOut)
def update_account(request: HttpRequest, account_id: UUID, payload: AccountPatch) -> dict[str, Any]:
    """Freeze or unfreeze. PATCH is idempotent by nature, so it takes no key."""
    account = accounts.set_status(client_of(request), account_id, payload.status)
    return presenters.account(account)


@router.get("/accounts/{account_id}/balance", response=BalanceOut)
def get_balance(
    request: HttpRequest, account_id: UUID, as_of: datetime | None = None
) -> dict[str, Any]:
    account = accounts.get_account(client_of(request), account_id)
    if as_of is not None:
        return presenters.historical_balance(account, balances.balance_as_of(account, as_of), as_of)
    return presenters.balance(account, balances.get_balance(account))


class EntryQuery(Schema):
    cursor: str | None = None
    limit: int = NinjaField(50, ge=1, le=200)


@router.get("/accounts/{account_id}/entries", response=EntryPage)
def list_entries(
    request: HttpRequest, account_id: UUID, query: Query[EntryQuery]
) -> dict[str, Any]:
    """Entries newest first, paged by an opaque cursor."""
    account = accounts.get_account(client_of(request), account_id)
    rows = Entry.objects.filter(account=account).order_by("-id")
    if query.cursor:
        rows = rows.filter(id__lt=pagination.decode(query.cursor))
    page = list(rows[: query.limit + 1])
    has_more = len(page) > query.limit
    page = page[: query.limit]
    return {
        "items": presenters.entries(page, {account.currency_id: account.currency.scale}),
        "next_cursor": pagination.encode(page[-1].id) if has_more else None,
    }
