"""The transactional outbox.

A change and the event announcing it are written in one database transaction,
so there is never a posted transfer without its event, or an event for a
transfer that rolled back. Publishing happens later, from ``outbox_worker``.
"""

from typing import Any
from uuid import UUID

from ledger.models import OutboxEvent


def enqueue(
    event_type: str, *, aggregate_type: str, aggregate_id: UUID, payload: dict[str, Any]
) -> OutboxEvent:
    """Record an event. Call inside the transaction that makes the change."""
    return OutboxEvent.objects.create(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
    )
