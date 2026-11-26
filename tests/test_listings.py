"""GET /transactions and GET /holds: filters, scoping, keyset paging."""

import base64
import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone

from ledger.models import Hold
from ledger.services import clients
from tests.test_api import deposit, open_account, post


def ids(response: Any) -> list[str]:
    return [item["id"] for item in response.json()["items"]]


def test_transactions_list_newest_first_and_pages_without_gaps(http: Client) -> None:
    account = open_account(http)
    made = [deposit(http, account["id"], str(n))["id"] for n in range(1, 6)]

    first = http.get("/v1/transactions", {"limit": 2})
    second = http.get("/v1/transactions", {"limit": 2, "cursor": first.json()["next_cursor"]})
    third = http.get("/v1/transactions", {"limit": 2, "cursor": second.json()["next_cursor"]})

    assert ids(first) + ids(second) + ids(third) == made[::-1]
    assert third.json()["next_cursor"] is None
    assert first.json()["items"][0]["entries"]


def test_transactions_filter_by_account_kind_and_time(http: Client) -> None:
    a, b = open_account(http), open_account(http)
    dep_a = deposit(http, a["id"], "10")
    deposit(http, b["id"], "20")
    transfer = post(
        http, "/v1/transfers", {"from": a["id"], "to": b["id"], "amount": "1", "currency": "USD"}
    ).json()

    by_account = ids(http.get("/v1/transactions", {"account_id": a["id"]}))
    by_kind = ids(http.get("/v1/transactions", {"kind": "transfer"}))
    since = (timezone.now() + timedelta(hours=1)).isoformat()
    future = ids(http.get("/v1/transactions", {"created_from": since}))

    assert by_account == [transfer["id"], dep_a["id"]]
    assert by_kind == [transfer["id"]]
    assert future == []


def test_transactions_of_another_client_are_invisible(http: Client) -> None:
    mine = open_account(http)
    deposit(http, mine["id"], "5")
    _, token = clients.create_client("other")
    other = Client(headers={"Authorization": f"Bearer {token}"})

    assert other.get("/v1/transactions").json()["items"] == []
    assert other.get("/v1/transactions", {"account_id": mine["id"]}).status_code == 404


def cursor_of(value: object) -> str:
    raw = json.dumps({"before": value}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.mark.parametrize(
    "cursor",
    [
        "nonsense",
        cursor_of(["2026-01-01T00:00:00+00:00", 5]),
        cursor_of(["2026-01-01T00:00:00", str(uuid4())]),
        cursor_of(7),
    ],
)
def test_a_malformed_cursor_is_a_400(http: Client, cursor: str) -> None:
    assert http.get("/v1/transactions", {"cursor": cursor}).status_code == 400
    assert http.get("/v1/holds", {"cursor": cursor}).status_code == 400


def place(http: Client, account_id: str, **body: Any) -> dict[str, Any]:
    response = post(
        http,
        "/v1/holds",
        {"account_id": account_id, "amount": "1", "currency": "USD", **body},
    )
    assert response.status_code == 201, response.content
    return response.json()  # type: ignore[no-any-return]


def test_holds_filter_by_status_with_overdue_reading_as_expired(http: Client) -> None:
    account = open_account(http)
    deposit(http, account["id"], "100")
    active = place(http, account["id"])
    released = place(http, account["id"])
    overdue = place(
        http, account["id"], expires_at=(timezone.now() + timedelta(hours=1)).isoformat()
    )
    post(http, f"/v1/holds/{released['id']}/release", {})
    Hold.objects.filter(id=overdue["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))

    def listed(status: str) -> list[str]:
        return ids(http.get("/v1/holds", {"account_id": account["id"], "status": status}))

    assert listed("active") == [active["id"]]
    assert listed("released") == [released["id"]]
    assert listed("expired") == [overdue["id"]]
    assert set(ids(http.get("/v1/holds"))) == {active["id"], released["id"], overdue["id"]}


def test_holds_page_newest_first(http: Client) -> None:
    account = open_account(http)
    deposit(http, account["id"], "100")
    made = [place(http, account["id"])["id"] for _ in range(3)]

    first = http.get("/v1/holds", {"limit": 2})
    second = http.get("/v1/holds", {"limit": 2, "cursor": first.json()["next_cursor"]})

    assert ids(first) + ids(second) == made[::-1]
    assert second.json()["next_cursor"] is None
