import hashlib
import hmac
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from ledger.models import OutboxEvent
from ledger.services import outbox
from tests.conftest import AccountFactory, fund

SECRET = "s3cret"


@contextmanager
def receiver(status: int = 200) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append({"body": body, "headers": dict(self.headers)})
            self.send_response(status)
            if status in (301, 302, 303):
                self.send_header("Location", "/ok")
            self.end_headers()

        def do_GET(self) -> None:  # where a followed redirect would land
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/hook", seen
    finally:
        server.shutdown()
        server.server_close()


def test_events_are_posted_signed_and_marked_published(make_account: AccountFactory) -> None:
    fund(make_account(), "5")
    with receiver() as (url, seen):
        published = outbox.publish_pending(outbox.WebhookPublisher(url, SECRET))

    assert published == 1
    body, headers = seen[0]["body"], seen[0]["headers"]
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert headers["X-Ledger-Signature"] == f"sha256={expected}"
    assert headers["X-Ledger-Event-Id"] == json.loads(body)["id"]
    assert not OutboxEvent.objects.filter(published_at__isnull=True).exists()


def test_a_failing_receiver_leaves_the_event_for_retry(make_account: AccountFactory) -> None:
    fund(make_account(), "5")
    with receiver(status=500) as (url, _):
        published = outbox.publish_pending(outbox.WebhookPublisher(url, SECRET))

    event = OutboxEvent.objects.get()
    assert published == 0
    assert event.published_at is None
    assert event.attempts == 1
    assert "500" in event.last_error


def test_a_redirect_is_a_failure_not_a_delivery(make_account: AccountFactory) -> None:
    fund(make_account(), "5")
    with receiver(status=302) as (url, seen):
        published = outbox.publish_pending(outbox.WebhookPublisher(url, SECRET))

    assert published == 0
    assert len(seen) == 1  # the POST only; the redirect was not followed
    assert OutboxEvent.objects.get().published_at is None


@pytest.mark.parametrize(("url", "secret"), [("file:///etc/passwd", SECRET), ("http://x", "")])
def test_an_unsafe_or_incomplete_webhook_config_is_refused(url: str, secret: str) -> None:
    with pytest.raises(ImproperlyConfigured):
        outbox.WebhookPublisher(url, secret)


@override_settings(
    LEDGER_OUTBOX_PUBLISHER="webhook", LEDGER_WEBHOOK_URL="http://x/hook", LEDGER_WEBHOOK_SECRET="k"
)
def test_the_webhook_publisher_is_selected_from_settings() -> None:
    assert isinstance(outbox.publisher_from_settings(), outbox.WebhookPublisher)
