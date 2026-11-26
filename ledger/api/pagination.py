"""Opaque keyset cursors.

Offsets drift when rows are inserted between page reads, and entries are
inserted constantly. A cursor names the last id seen instead, so a page is
always "the next N entries older than this one", however much was posted in
the meantime.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from django.db.models import Model, Q, QuerySet

from ledger import errors

M = TypeVar("M", bound=Model)


def encode(last_id: int) -> str:
    return _pack(last_id)


def decode(cursor: str) -> int:
    before = _unpack(cursor)
    if not isinstance(before, int) or isinstance(before, bool) or before < 1:
        raise errors.InvalidCursor("cursor is malformed")
    return before


def _pack(value: Any) -> str:
    raw = json.dumps({"before": value}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unpack(cursor: str) -> Any:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))["before"]
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise errors.InvalidCursor("cursor is malformed") from None


def page_newest_first(
    rows: "QuerySet[M]", cursor: str | None, limit: int
) -> tuple[list[M], str | None]:
    """One page of ``rows`` ordered by (created_at, id) descending, plus the next cursor.

    For tables keyed by UUID, where the id alone says nothing about order.
    ``rows`` must have ``created_at`` and ``id`` fields.
    """
    rows = rows.order_by("-created_at", "-id")
    if cursor:
        stamp, last_id = _decode_stamp(cursor)
        rows = rows.filter(Q(created_at__lt=stamp) | Q(created_at=stamp, id__lt=last_id))
    page = list(rows[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    next_cursor = _pack([page[-1].created_at.isoformat(), str(page[-1].id)]) if has_more else None  # type: ignore[attr-defined]
    return page, next_cursor


def _decode_stamp(cursor: str) -> tuple[datetime, UUID]:
    value = _unpack(cursor)
    try:
        stamp, last_id = datetime.fromisoformat(value[0]), UUID(value[1])
    except (ValueError, TypeError, IndexError, KeyError, AttributeError):
        raise errors.InvalidCursor("cursor is malformed") from None
    if stamp.tzinfo is None:
        raise errors.InvalidCursor("cursor is malformed")
    return stamp, last_id
