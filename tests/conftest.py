from collections.abc import Callable

import pytest

from ledger.models import Account, AccountType, ApiClient, Currency

TEST_CURRENCIES = {"USD": 2, "EUR": 2, "JPY": 0, "BTC": 8}


def _ensure_currencies() -> None:
    # Transactional tests truncate every table, seeded currencies included.
    for code, scale in TEST_CURRENCIES.items():
        Currency.objects.get_or_create(code=code, defaults={"scale": scale})


@pytest.fixture
def api_client_record(db: None) -> ApiClient:
    return ApiClient.objects.create(name="acme", key_hash="0" * 64)


AccountFactory = Callable[..., Account]


@pytest.fixture
def make_account(api_client_record: ApiClient) -> AccountFactory:
    _ensure_currencies()

    def make(
        currency: str = "USD",
        kind: AccountType = AccountType.LIABILITY,
        **fields: object,
    ) -> Account:
        return Account.objects.create(
            client=api_client_record,
            owner_id=str(fields.pop("owner_id", "owner-1")),
            currency_id=currency,
            type=kind,
            **fields,
        )

    return make
