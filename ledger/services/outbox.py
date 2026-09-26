"""The transactional outbox.

A change and the event announcing it are written in one database transaction,
so there is never a posted transfer without its event, or an event for a
transfer that rolled back. Publishing happens later, from ``outbox_worker``.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from ledger.models import OutboxEvent

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


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


class Publisher(Protocol):
    def publish(self, event: OutboxEvent) -> None:
        """Deliver one event, raising if it may not have arrived."""


class LogPublisher:
    """Writes each event to the log. The default, and enough for a demo."""

    def publish(self, event: OutboxEvent) -> None:
        logger.info("event %s", json.dumps(message(event), sort_keys=True))


class RedisStreamPublisher:
    """Appends each event to a Redis stream (``XADD``)."""

    def __init__(self, redis_client: "Redis", stream: str) -> None:
        self._redis = redis_client
        self._stream = stream

    def publish(self, event: OutboxEvent) -> None:
        self._redis.xadd(self._stream, {"event": json.dumps(message(event))})


def message(event: OutboxEvent) -> dict[str, Any]:
    """The wire format. ``id`` is stable across redeliveries: dedupe on it."""
    return {
        "id": str(event.id),
        "type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": str(event.aggregate_id),
        "occurred_at": event.created_at.isoformat(),
        "data": event.payload,
    }


def publisher_from_settings() -> Publisher:
    kind = settings.LEDGER_OUTBOX_PUBLISHER
    if kind == "log":
        return LogPublisher()
    if kind == "redis":
        from redis import Redis  # noqa: PLC0415 - optional dependency

        return RedisStreamPublisher(
            Redis.from_url(settings.REDIS_URL), settings.LEDGER_OUTBOX_STREAM
        )
    raise ImproperlyConfigured(f"unknown LEDGER_OUTBOX_PUBLISHER {kind!r}")


def publish_pending(publisher: Publisher, *, batch_size: int = 100) -> int:
    """Publish one batch of unpublished events, oldest first. Returns how many.

    Rows are claimed with ``FOR UPDATE SKIP LOCKED``, so several workers can
    run side by side without sending the same event twice at once. An event
    is marked published only after ``publish`` returns; a crash in between
    means it is sent again next time. That is the at-least-once contract.

    On the first failure the batch stops, so events are never published out
    of order; the failure is recorded on the event and retried next run.
    """
    published = 0
    with transaction.atomic():
        batch = (
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(published_at__isnull=True)
            .order_by("created_at", "id")[:batch_size]
        )
        for event in batch:
            try:
                publisher.publish(event)
            except Exception as exc:
                event.attempts += 1
                event.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                event.save(update_fields=["attempts", "last_error"])
                logger.warning("publishing %s failed: %s", event.id, event.last_error)
                break
            event.attempts += 1
            event.published_at = timezone.now()
            event.save(update_fields=["attempts", "published_at"])
            published += 1
    return published
