"""Races against a real Postgres.

These tests commit for real (``transaction=True``) and run each worker on its
own thread, hence its own database connection. Nothing here is mocked: if the
locking were wrong, these would overdraw, deadlock or double-post.

Worker count stays under Postgres' default ``max_connections`` of 100 so the
suite runs against a stock server; the number of racing operations does not.
"""

import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.db import connections
from django.test import Client

from ledger.models import Account, ApiClient, OutboxEvent, Transaction
from ledger.services import balances, clients, holds, outbox, payments, reversals
from tests.conftest import account_factory, balance_of, ensure_currencies, fund

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 40


def race[T](task: Callable[[int], T], times: int) -> list[T | BaseException]:
    """Run ``task(i)`` for i in range(times) on WORKERS threads, released together."""
    gate = threading.Barrier(min(times, WORKERS))

    def run(i: int) -> T | BaseException:
        try:
            if i < WORKERS:
                gate.wait(timeout=30)
            return task(i)
        except Exception as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(run, range(times)))


def outcome(result: object) -> str:
    return type(result).__name__ if isinstance(result, BaseException) else "ok"


@pytest.fixture
def client_record() -> ApiClient:
    return ApiClient.objects.create(name=f"race-{uuid4()}", key_hash=uuid4().hex * 2)


@pytest.fixture
def wallet(client_record: ApiClient) -> Account:
    return account_factory(client_record)()


def test_a_hundred_concurrent_withdrawals_never_overdraw(
    client_record: ApiClient, wallet: Account
) -> None:
    fund(wallet, "10")

    results = race(
        lambda _: payments.withdraw(
            client=client_record, account_id=wallet.id, amount=Decimal(1), currency="USD"
        ),
        times=100,
    )

    assert Counter(map(outcome, results)) == {"ok": 10, "InsufficientFunds": 90}
    assert balance_of(wallet) == 0


def test_opposing_transfers_do_not_deadlock(client_record: ApiClient) -> None:
    make = account_factory(client_record)
    alice, bob = make(), make()
    fund(alice, "1000")
    fund(bob, "1000")

    def shuffle(i: int) -> Transaction:
        source, destination = (alice, bob) if i % 2 else (bob, alice)
        return payments.transfer(
            client=client_record,
            source_id=source.id,
            destination_id=destination.id,
            amount=Decimal(1),
            currency="USD",
        )

    results = race(shuffle, times=200)

    # Without a fixed lock order, half of these would lock A then B while the
    # other half locked B then A, and Postgres would abort some as deadlocks.
    assert Counter(map(outcome, results)) == {"ok": 200}
    assert balance_of(alice) + balance_of(bob) == Decimal(2000)


def test_holds_and_withdrawals_share_one_available_balance(
    client_record: ApiClient, wallet: Account
) -> None:
    fund(wallet, "10")

    def spend(i: int) -> object:
        if i % 2:
            return holds.create_hold(
                client=client_record, account_id=wallet.id, amount=Decimal(1), currency="USD"
            )
        return payments.withdraw(
            client=client_record, account_id=wallet.id, amount=Decimal(1), currency="USD"
        )

    results = race(spend, times=40)

    assert Counter(map(outcome, results)) == {"ok": 10, "InsufficientFunds": 30}
    balance = balances.get_balance(wallet)
    assert balance.available == 0
    assert balance.ledger == balance.held


def test_a_transaction_is_reversed_at_most_once_under_contention(
    client_record: ApiClient, wallet: Account
) -> None:
    fund(wallet, "10")
    deposit_id = wallet.entries.get().transaction_id

    results = race(
        lambda _: reversals.reverse(client=client_record, transaction_id=deposit_id),
        times=20,
    )

    assert Counter(map(outcome, results)) == {"ok": 1, "AlreadyReversed": 19}
    assert balance_of(wallet) == 0


def test_fifty_identical_requests_post_one_transaction() -> None:
    ensure_currencies()
    _, token = clients.create_client("idempotency-race")
    auth = {"Authorization": f"Bearer {token}"}
    account = Client(headers=auth).post(
        "/v1/accounts",
        {"owner_id": "racer", "currency": "USD"},
        content_type="application/json",
        headers={"Idempotency-Key": str(uuid4())},
    )
    body = {"account_id": account.json()["id"], "amount": "25.00", "currency": "USD"}
    key = str(uuid4())

    def deposit(_: int) -> Any:
        return Client(headers=auth).post(
            "/v1/deposits", body, content_type="application/json", headers={"Idempotency-Key": key}
        )

    responses = race(deposit, times=50)

    assert all(outcome(r) == "ok" for r in responses)
    assert {r.status_code for r in responses} == {201}  # type: ignore[union-attr]
    assert len({r.content for r in responses}) == 1  # type: ignore[union-attr]
    replayed = [r.has_header("Idempotent-Replayed") for r in responses]  # type: ignore[union-attr]
    assert replayed.count(False) == 1
    assert Transaction.objects.filter(kind="deposit").count() == 1


def test_parallel_outbox_workers_publish_each_event_once(
    client_record: ApiClient, wallet: Account
) -> None:
    for _ in range(60):
        fund(wallet, "1")
    seen: list[str] = []
    lock = threading.Lock()

    class Collector:
        def publish(self, event: OutboxEvent) -> None:
            with lock:
                seen.append(str(event.id))

    def work(_: int) -> int:
        return sum(iter(lambda: outbox.publish_pending(Collector(), batch_size=5), 0))

    race(work, times=4)

    assert len(seen) == len(set(seen)) == OutboxEvent.objects.count() == 60
