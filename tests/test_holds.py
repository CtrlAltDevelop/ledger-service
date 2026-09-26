from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from ledger import errors
from ledger.models import Account, ApiClient, Hold, HoldStatus, OutboxEvent, TransactionKind
from ledger.services import balances, holds, payments
from tests.conftest import AccountFactory, balance_of, fund
from tests.test_api import open_account, post


@pytest.fixture
def funded(make_account: AccountFactory) -> Account:
    wallet = make_account()
    fund(wallet, "100")
    return wallet


def place(account: Account, amount: str, **kwargs: object) -> Hold:
    return holds.create_hold(
        client=account.client,
        account_id=account.id,
        amount=Decimal(amount),
        currency=account.currency_id,
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_hold_lowers_available_but_not_ledger(funded: Account) -> None:
    place(funded, "30")

    balance = balances.get_balance(funded)
    assert balance.ledger == Decimal("100")
    assert balance.held == Decimal("30")
    assert balance.available == Decimal("70")


def test_a_hold_beyond_available_is_refused(funded: Account) -> None:
    place(funded, "60")

    with pytest.raises(errors.InsufficientFunds):
        place(funded, "41")


def test_held_funds_cannot_be_withdrawn(api_client_record: ApiClient, funded: Account) -> None:
    place(funded, "60")

    with pytest.raises(errors.InsufficientFunds):
        payments.withdraw(
            client=api_client_record, account_id=funded.id, amount=Decimal("41"), currency="USD"
        )


def test_capture_moves_the_money_and_frees_the_hold(
    api_client_record: ApiClient, funded: Account, make_account: AccountFactory
) -> None:
    merchant = make_account()
    hold = place(funded, "100")  # the whole balance, so the capture must not count it twice

    captured = holds.capture_hold(
        client=api_client_record, hold_id=hold.id, destination_id=merchant.id
    )

    assert captured.status == HoldStatus.CAPTURED
    assert captured.capture_transaction is not None
    assert captured.capture_transaction.kind == TransactionKind.HOLD_CAPTURE
    assert balances.get_balance(funded).available == 0
    assert balance_of(merchant) == Decimal("100")


def test_partial_capture_releases_the_remainder(
    api_client_record: ApiClient, funded: Account, make_account: AccountFactory
) -> None:
    hold = place(funded, "40")

    holds.capture_hold(
        client=api_client_record,
        hold_id=hold.id,
        destination_id=make_account().id,
        amount=Decimal("25"),
    )

    balance = balances.get_balance(funded)
    assert balance.ledger == Decimal("75")
    assert balance.held == 0


def test_capture_more_than_held_is_refused(
    api_client_record: ApiClient, funded: Account, make_account: AccountFactory
) -> None:
    hold = place(funded, "10")

    with pytest.raises(errors.InvalidAmount):
        holds.capture_hold(
            client=api_client_record,
            hold_id=hold.id,
            destination_id=make_account().id,
            amount=Decimal("10.01"),
        )


def test_release_frees_the_funds(api_client_record: ApiClient, funded: Account) -> None:
    hold = place(funded, "10")

    released = holds.release_hold(client=api_client_record, hold_id=hold.id)

    assert released.status == HoldStatus.RELEASED
    assert balances.get_balance(funded).available == Decimal("100")
    assert list(
        OutboxEvent.objects.filter(aggregate_id=hold.id).values_list("event_type", flat=True)
    ) == ["hold.created", "hold.released"]


def test_a_resolved_hold_cannot_be_resolved_again(
    api_client_record: ApiClient, funded: Account, make_account: AccountFactory
) -> None:
    hold = place(funded, "10")
    holds.release_hold(client=api_client_record, hold_id=hold.id)

    with pytest.raises(errors.HoldNotActive):
        holds.release_hold(client=api_client_record, hold_id=hold.id)
    with pytest.raises(errors.HoldNotActive):
        holds.capture_hold(
            client=api_client_record, hold_id=hold.id, destination_id=make_account().id
        )


def test_an_expired_hold_stops_counting_and_cannot_be_captured(
    api_client_record: ApiClient, funded: Account, make_account: AccountFactory
) -> None:
    hold = place(funded, "10", expires_at=timezone.now() + timedelta(minutes=5))
    Hold.objects.filter(id=hold.id).update(expires_at=timezone.now() - timedelta(seconds=1))
    hold.refresh_from_db()

    assert holds.effective_status(hold) == "expired"
    assert balances.get_balance(funded).available == Decimal("100")
    with pytest.raises(errors.HoldNotActive, match="expired"):
        holds.capture_hold(
            client=api_client_record, hold_id=hold.id, destination_id=make_account().id
        )


def test_an_expiry_in_the_past_is_refused(funded: Account) -> None:
    with pytest.raises(errors.InvalidExpiry):
        place(funded, "1", expires_at=timezone.now() - timedelta(seconds=1))


def test_holds_over_http(http: Client) -> None:
    buyer, seller = open_account(http)["id"], open_account(http)["id"]
    post(http, "/v1/deposits", {"account_id": buyer, "amount": "50", "currency": "USD"})

    created = post(http, "/v1/holds", {"account_id": buyer, "amount": "20", "currency": "USD"})
    hold_id = created.json()["id"]
    captured = post(http, f"/v1/holds/{hold_id}/capture", {"to": seller, "amount": "15"})
    again = post(http, f"/v1/holds/{hold_id}/release", {})

    assert created.status_code == 201
    assert created.json()["status"] == "active"
    assert created.json()["amount"] == "20.00"
    assert captured.status_code == 200
    assert captured.json()["status"] == "captured"
    assert http.get(f"/v1/holds/{hold_id}").json() == captured.json()
    assert again.status_code == 409
    assert again.json()["code"] == "hold_not_active"
    assert http.get(f"/v1/accounts/{seller}/balance").json()["ledger"] == "15.00"
    assert http.get(f"/v1/accounts/{buyer}/balance").json()["available"] == "35.00"
