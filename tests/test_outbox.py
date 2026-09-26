import json
from decimal import Decimal
from io import StringIO
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import override_settings

from ledger import errors
from ledger.models import ApiClient, OutboxEvent
from ledger.services import outbox, payments
from tests.conftest import AccountFactory, fund


class Recorder:
    def __init__(self, fail_on: int | None = None) -> None:
        self.sent: list[str] = []
        self.fail_on = fail_on

    def publish(self, event: OutboxEvent) -> None:
        if self.fail_on is not None and len(self.sent) == self.fail_on:
            raise ConnectionError("broker unavailable")
        self.sent.append(event.event_type)


class Crash(BaseException):
    """Stands in for the process dying: not an Exception, so nothing catches it."""


class CrashAfterSending:
    def __init__(self) -> None:
        self.sent: list[OutboxEvent] = []

    def publish(self, event: OutboxEvent) -> None:
        self.sent.append(event)
        raise Crash


@pytest.fixture
def three_events(make_account: AccountFactory) -> list[OutboxEvent]:
    wallet = make_account()
    fund(wallet, "1")
    fund(wallet, "2")
    fund(wallet, "3")
    return list(OutboxEvent.objects.order_by("created_at"))


def test_events_are_published_in_order_and_marked(three_events: list[OutboxEvent]) -> None:
    recorder = Recorder()

    published = outbox.publish_pending(recorder)

    assert published == 3
    assert recorder.sent == ["transaction.posted"] * 3
    assert not OutboxEvent.objects.filter(published_at__isnull=True).exists()
    assert outbox.publish_pending(recorder) == 0


def test_a_failure_stops_the_batch_and_is_retried(three_events: list[OutboxEvent]) -> None:
    published = outbox.publish_pending(Recorder(fail_on=1))

    assert published == 1
    stuck = OutboxEvent.objects.get(id=three_events[1].id)
    assert stuck.published_at is None
    assert stuck.attempts == 1
    assert "broker unavailable" in stuck.last_error
    # Nothing after the failure was skipped ahead of it.
    assert OutboxEvent.objects.get(id=three_events[2].id).published_at is None

    assert outbox.publish_pending(Recorder()) == 2


def test_a_crash_after_sending_means_the_event_is_sent_again(
    three_events: list[OutboxEvent],
) -> None:
    crashing = CrashAfterSending()
    with pytest.raises(Crash):
        outbox.publish_pending(crashing)

    # The broker got the first event, but its "published" mark rolled back with
    # the crash, so the next worker sends it again: at least once, not exactly.
    retry = Recorder()
    outbox.publish_pending(retry)
    assert crashing.sent[0].id == three_events[0].id
    assert len(retry.sent) == 3


def test_a_rolled_back_posting_leaves_no_event(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet = make_account()

    with pytest.raises(errors.InsufficientFunds):
        payments.withdraw(
            client=api_client_record, account_id=wallet.id, amount=Decimal(1), currency="USD"
        )

    assert not OutboxEvent.objects.exists()


def test_the_wire_message_carries_a_stable_id(three_events: list[OutboxEvent]) -> None:
    event = three_events[0]

    message = outbox.message(event)

    assert message["id"] == str(event.id)
    assert message["type"] == "transaction.posted"
    assert message["data"]["entries"][0]["currency"] == "USD"


def test_redis_publisher_appends_to_the_stream(three_events: list[OutboxEvent]) -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def xadd(self, stream: str, fields: dict[str, Any]) -> None:
            self.calls.append((stream, fields))

    fake = FakeRedis()
    publisher = outbox.RedisStreamPublisher(fake, "ledger.events")  # type: ignore[arg-type]

    outbox.publish_pending(publisher)

    assert [stream for stream, _ in fake.calls] == ["ledger.events"] * 3
    assert json.loads(fake.calls[0][1]["event"])["id"] == str(three_events[0].id)


def test_worker_command_drains_the_outbox_once(three_events: list[OutboxEvent]) -> None:
    out = StringIO()

    call_command("outbox_worker", "--once", "--batch-size", "2", stdout=out)

    assert "published 3 event(s)" in out.getvalue()
    assert not OutboxEvent.objects.filter(published_at__isnull=True).exists()


@override_settings(LEDGER_OUTBOX_PUBLISHER="carrier-pigeon")
def test_an_unknown_publisher_is_a_configuration_error() -> None:
    with pytest.raises(ImproperlyConfigured):
        outbox.publisher_from_settings()
