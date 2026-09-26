"""Opening accounts and finding them again."""

from uuid import UUID

from django.db import transaction

from ledger import errors
from ledger.models import Account, AccountStatus, AccountType, ApiClient, Currency
from ledger.services import outbox


def get_currency(code: str) -> Currency:
    try:
        return Currency.objects.get(code=code)
    except Currency.DoesNotExist:
        raise errors.UnknownCurrency(f"{code!r} is not a supported currency") from None


def get_account(client: ApiClient, account_id: UUID) -> Account:
    try:
        return Account.objects.select_related("currency").get(id=account_id, client=client)
    except Account.DoesNotExist:
        raise errors.NotFound(f"account {account_id} does not exist") from None


def require_currency(account: Account, currency: str) -> None:
    if account.currency_id != currency:
        raise errors.CurrencyMismatch(
            f"account {account.id} holds {account.currency_id}, not {currency}",
            account_id=str(account.id),
        )


def create_account(
    *,
    client: ApiClient,
    owner_id: str,
    currency: str,
    type: AccountType,
    allow_overdraft: bool = False,
) -> Account:
    with transaction.atomic():
        account = Account.objects.create(
            client=client,
            owner_id=owner_id,
            currency=get_currency(currency),
            type=type,
            allow_overdraft=allow_overdraft,
        )
        outbox.enqueue(
            "account.created",
            aggregate_type="account",
            aggregate_id=account.id,
            payload={
                "account_id": str(account.id),
                "owner_id": owner_id,
                "currency": currency,
                "type": type,
            },
        )
    return account


def set_status(client: ApiClient, account_id: UUID, status: AccountStatus) -> Account:
    """Freeze or unfreeze an account. Freezing blocks outflows, not inflows."""
    account = get_account(client, account_id)
    if account.status != status:
        account.status = status
        account.save(update_fields=["status"])
    return account


def settlement_account(client: ApiClient, currency: Currency) -> Account:
    """The asset account that mirrors a client's money held outside the ledger.

    Deposits debit it and withdrawals credit it, so it tracks what the ledger
    owes customers in that currency. Created on first use.
    """
    account, _ = Account.objects.get_or_create(
        client=client,
        currency=currency,
        is_settlement=True,
        defaults={
            "owner_id": "settlement",
            "type": AccountType.ASSET,
            "allow_overdraft": True,
        },
    )
    return account
