import json
from typing import Any
from uuid import uuid4

import pytest
from django.test import Client

from ledger.models import IdempotencyRecord, Transaction
from ledger.services import clients
from tests.test_api import JSON, open_account, post


def send(http: Client, url: str, body: dict[str, Any] | str, key: str) -> Any:
    data = body if isinstance(body, str) else json.dumps(body)
    return http.post(url, data, content_type=JSON, headers={"Idempotency-Key": key})


@pytest.fixture
def wallet(http: Client) -> str:
    return str(open_account(http)["id"])


def test_a_post_without_a_key_is_refused(http: Client, wallet: str) -> None:
    response = http.post(
        "/v1/deposits",
        {"account_id": wallet, "amount": "1", "currency": "USD"},
        content_type=JSON,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"
    assert not Transaction.objects.exists()


@pytest.mark.parametrize("key", ["has space", "x" * 256, "naïve"])
def test_malformed_keys_are_refused(http: Client, wallet: str, key: str) -> None:
    response = send(
        http, "/v1/deposits", {"account_id": wallet, "amount": "1", "currency": "USD"}, key
    )

    assert response.json()["code"] == "idempotency_key_required"


def test_a_retry_replays_the_first_response(http: Client, wallet: str) -> None:
    body = {"account_id": wallet, "amount": "10", "currency": "USD"}
    key = str(uuid4())

    first = send(http, "/v1/deposits", body, key)
    retry = send(http, "/v1/deposits", body, key)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry["Idempotent-Replayed"] == "true"
    assert not first.has_header("Idempotent-Replayed")
    assert Transaction.objects.filter(kind="deposit").count() == 1


def test_key_order_in_the_body_does_not_matter(http: Client, wallet: str) -> None:
    key = str(uuid4())
    send(http, "/v1/deposits", f'{{"account_id":"{wallet}","amount":"1","currency":"USD"}}', key)

    retry = send(
        http, "/v1/deposits", f'{{"currency": "USD", "amount": "1", "account_id": "{wallet}"}}', key
    )

    assert retry.status_code == 201
    assert retry["Idempotent-Replayed"] == "true"


def test_the_same_key_with_a_different_body_is_a_conflict(http: Client, wallet: str) -> None:
    key = str(uuid4())
    send(http, "/v1/deposits", {"account_id": wallet, "amount": "10", "currency": "USD"}, key)

    response = send(
        http, "/v1/deposits", {"account_id": wallet, "amount": "11", "currency": "USD"}, key
    )

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_conflict"


def test_the_same_key_on_another_endpoint_is_a_conflict(http: Client, wallet: str) -> None:
    key = str(uuid4())
    body = {"account_id": wallet, "amount": "10", "currency": "USD"}
    send(http, "/v1/deposits", body, key)

    response = send(http, "/v1/withdrawals", body, key)

    assert response.json()["code"] == "idempotency_conflict"


def test_a_failed_request_replays_its_failure(http: Client, wallet: str) -> None:
    body = {"account_id": wallet, "amount": "5", "currency": "USD"}
    key = str(uuid4())
    first = send(http, "/v1/withdrawals", body, key)

    # Funds arrive, but a retry of the same request still gets the same answer:
    # a retry is the same request, not a new one.
    post(http, "/v1/deposits", {**body, "amount": "50"})
    retry = send(http, "/v1/withdrawals", body, key)

    assert first.status_code == retry.status_code == 422
    assert retry.json() == first.json()
    assert retry["Content-Type"] == "application/problem+json"
    assert retry["Idempotent-Replayed"] == "true"


def test_a_validation_error_does_not_burn_the_key(http: Client, wallet: str) -> None:
    key = str(uuid4())
    send(http, "/v1/deposits", {"account_id": wallet, "amount": "oops", "currency": "USD"}, key)

    response = send(
        http, "/v1/deposits", {"account_id": wallet, "amount": "1", "currency": "USD"}, key
    )

    assert response.status_code == 201
    assert IdempotencyRecord.objects.count() == 2  # account creation + this deposit


def test_keys_are_scoped_to_the_client(http: Client, wallet: str) -> None:
    key = str(uuid4())
    send(http, "/v1/accounts", {"owner_id": "a", "currency": "USD"}, key)
    _, token = clients.create_client("second")
    other = Client(headers={"Authorization": f"Bearer {token}"})

    response = send(other, "/v1/accounts", {"owner_id": "b", "currency": "USD"}, key)

    assert response.status_code == 201
    assert not response.has_header("Idempotent-Replayed")
