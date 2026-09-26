"""The HTTP surface: status codes, bodies, problem details, pagination."""

from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from ledger.services import clients

JSON = "application/json"


def open_account(http: Client, **fields: Any) -> dict[str, Any]:
    body = {"owner_id": "user-1", "currency": "USD", **fields}
    response = http.post("/v1/accounts", body, content_type=JSON)
    assert response.status_code == 201, response.content
    return response.json()  # type: ignore[no-any-return]


def deposit(http: Client, account_id: str, amount: str) -> dict[str, Any]:
    response = http.post(
        "/v1/deposits",
        {"account_id": account_id, "amount": amount, "currency": "USD"},
        content_type=JSON,
    )
    assert response.status_code == 201, response.content
    return response.json()  # type: ignore[no-any-return]


def balance(http: Client, account_id: str) -> dict[str, Any]:
    return http.get(f"/v1/accounts/{account_id}/balance").json()  # type: ignore[no-any-return]


def test_requests_without_a_token_get_a_401_problem(db: None) -> None:
    response = Client().get("/v1/accounts/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 401
    assert response["Content-Type"] == "application/problem+json"
    assert response["WWW-Authenticate"] == "Bearer"
    assert response.json()["code"] == "unauthenticated"


def test_open_and_read_an_account(http: Client) -> None:
    created = open_account(http, type="liability")

    fetched = http.get(f"/v1/accounts/{created['id']}").json()

    assert fetched == created
    assert fetched["status"] == "active"
    assert balance(http, created["id"]) == {
        "account_id": created["id"],
        "currency": "USD",
        "ledger": "0.00",
        "held": "0.00",
        "available": "0.00",
        "as_of": None,
    }


def test_unknown_currency_is_a_422_problem(http: Client) -> None:
    response = http.post("/v1/accounts", {"owner_id": "u", "currency": "XXX"}, content_type=JSON)

    assert response.status_code == 422
    assert response.json()["type"] == "urn:ledger:problem:unknown_currency"


def test_deposit_transfer_withdraw_round_trip(http: Client) -> None:
    alice, bob = open_account(http)["id"], open_account(http)["id"]
    deposit(http, alice, "100")

    transfer = http.post(
        "/v1/transfers",
        {"from": alice, "to": bob, "amount": "12.50", "currency": "USD"},
        content_type=JSON,
    )
    withdrawal = http.post(
        "/v1/withdrawals",
        {"account_id": bob, "amount": "2.5", "currency": "USD"},
        content_type=JSON,
    )

    assert transfer.status_code == 201
    assert {e["amount"] for e in transfer.json()["entries"]} == {"12.50", "-12.50"}
    assert withdrawal.status_code == 201
    assert balance(http, alice)["available"] == "87.50"
    assert balance(http, bob)["available"] == "10.00"


def test_insufficient_funds_is_a_422_problem(http: Client) -> None:
    alice, bob = open_account(http)["id"], open_account(http)["id"]

    response = http.post(
        "/v1/transfers",
        {"from": alice, "to": bob, "amount": "1", "currency": "USD"},
        content_type=JSON,
    )

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "insufficient_funds"
    assert problem["status"] == 422
    assert problem["account_id"] == alice


def test_currency_mismatch_is_a_422_problem(http: Client) -> None:
    account = open_account(http)["id"]

    response = http.post(
        "/v1/deposits",
        {"account_id": account, "amount": "1", "currency": "EUR"},
        content_type=JSON,
    )

    assert response.json()["code"] == "currency_mismatch"


@pytest.mark.parametrize("amount", ["1.5e3", "-1", "12.", "abc", ""])
def test_malformed_amounts_fail_validation(http: Client, amount: str) -> None:
    account = open_account(http)["id"]

    response = http.post(
        "/v1/deposits",
        {"account_id": account, "amount": amount, "currency": "USD"},
        content_type=JSON,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["errors"][0]["loc"][-1] == "amount"


def test_amount_finer_than_currency_is_rejected(http: Client) -> None:
    account = open_account(http)["id"]

    response = http.post(
        "/v1/deposits",
        {"account_id": account, "amount": "1.001", "currency": "USD"},
        content_type=JSON,
    )

    assert response.json()["code"] == "invalid_amount"


def test_journal_entries_must_balance(http: Client) -> None:
    a = open_account(http, allow_overdraft=True)["id"]
    b = open_account(http)["id"]

    response = http.post(
        "/v1/transactions",
        {
            "entries": [
                {"account_id": a, "amount": "10", "currency": "USD"},
                {"account_id": b, "amount": "-9", "currency": "USD"},
            ]
        },
        content_type=JSON,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "unbalanced_transaction"
    assert response.json()["imbalance"] == {"USD": "1"}


def test_reverse_a_transaction(http: Client) -> None:
    account = open_account(http)["id"]
    original = deposit(http, account, "5")

    reversal = http.post(f"/v1/transactions/{original['id']}/reverse", {}, content_type=JSON)
    again = http.post(f"/v1/transactions/{original['id']}/reverse", {}, content_type=JSON)

    assert reversal.status_code == 201
    assert reversal.json()["reverses"] == original["id"]
    assert (
        http.get(f"/v1/transactions/{original['id']}").json()["reversed_by"]
        == (reversal.json()["id"])
    )
    assert again.status_code == 409
    assert again.json()["code"] == "already_reversed"
    assert balance(http, account)["ledger"] == "0.00"


def test_frozen_account_cannot_send(http: Client) -> None:
    account = open_account(http)["id"]
    deposit(http, account, "5")

    frozen = http.patch(f"/v1/accounts/{account}", {"status": "frozen"}, content_type=JSON)
    response = http.post(
        "/v1/withdrawals",
        {"account_id": account, "amount": "1", "currency": "USD"},
        content_type=JSON,
    )

    assert frozen.json()["status"] == "frozen"
    assert response.status_code == 409
    assert response.json()["code"] == "account_inactive"


def test_other_clients_cannot_see_an_account(http: Client) -> None:
    account = open_account(http)["id"]

    _, token = clients.create_client("intruder")
    response = Client(headers={"Authorization": f"Bearer {token}"}).get(f"/v1/accounts/{account}")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_historical_balance_ignores_later_entries(http: Client) -> None:
    account = open_account(http)["id"]
    deposit(http, account, "5")
    between = timezone.now()
    deposit(http, account, "7")

    response = http.get(f"/v1/accounts/{account}/balance", {"as_of": between.isoformat()}).json()

    assert response["ledger"] == "5.00"
    assert response["held"] is None


def test_entries_page_through_with_a_cursor(http: Client) -> None:
    account = open_account(http)["id"]
    for i in range(1, 6):
        deposit(http, account, str(i))

    seen: list[str] = []
    url, params = f"/v1/accounts/{account}/entries", {"limit": 2}
    while True:
        page = http.get(url, params).json()
        seen.extend(item["amount"] for item in page["items"])
        if page["next_cursor"] is None:
            break
        params = {"limit": 2, "cursor": page["next_cursor"]}

    # Newest first; deposits credit a liability account, hence negative.
    assert seen == ["-5.00", "-4.00", "-3.00", "-2.00", "-1.00"]


def test_a_forged_cursor_is_a_400_problem(http: Client) -> None:
    account = open_account(http)["id"]

    response = http.get(f"/v1/accounts/{account}/entries", {"cursor": "not-a-cursor"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_cursor"
