from collections.abc import Callable
from decimal import Decimal

import pytest
from django.test import Client

from ledger.models import Account, AccountType, ApiClient, Currency
from ledger.services import balances, clients, payments

TEST_CURRENCIES = {"USD": 2, "EUR": 2, "JPY": 0, "BTC": 8}


def ensure_currencies() -> None:
    # Transactional tests truncate every table, seeded currencies included.
    for code, scale in TEST_CURRENCIES.items():
        Currency.objects.get_or_create(code=code, defaults={"scale": scale})


@pytest.fixture
def api_client_record(db: None) -> ApiClient:
    return ApiClient.objects.create(name="acme", key_hash="0" * 64)


AccountFactory = Callable[..., Account]


def account_factory(client: ApiClient) -> AccountFactory:
    ensure_currencies()

    def make(
        currency: str = "USD",
        kind: AccountType = AccountType.LIABILITY,
        **fields: object,
    ) -> Account:
        return Account.objects.create(
            client=client,
            owner_id=str(fields.pop("owner_id", "owner-1")),
            currency_id=currency,
            type=kind,
            **fields,
        )

    return make


@pytest.fixture
def make_account(api_client_record: ApiClient) -> AccountFactory:
    return account_factory(api_client_record)


def balance_of(account: Account) -> Decimal:
    return balances.get_balance(account).ledger


def fund(account: Account, amount: str) -> None:
    payments.deposit(
        client=account.client,
        account_id=account.id,
        amount=Decimal(amount),
        currency=account.currency_id,
    )


@pytest.fixture
def http(db: None) -> Client:
    """A test client authenticated as a fresh API client."""
    ensure_currencies()
    _, token = clients.create_client("http-tests")
    return Client(headers={"Authorization": f"Bearer {token}"})
