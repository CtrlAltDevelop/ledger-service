"""Turn models into response dicts, with amounts rendered at currency scale."""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ledger.models import Account, Currency, Entry, Hold, Transaction
from ledger.money import format_amount
from ledger.services.balances import Balance
from ledger.services.holds import effective_status


def currency_scales() -> dict[str, int]:
    # A handful of rows; cheaper to read whole than to join per entry.
    return dict(Currency.objects.values_list("code", "scale"))


def account(acc: Account) -> dict[str, Any]:
    return {
        "id": acc.id,
        "owner_id": acc.owner_id,
        "currency": acc.currency_id,
        "type": acc.type,
        "status": acc.status,
        "allow_overdraft": acc.allow_overdraft,
        "created_at": acc.created_at,
    }


def balance(acc: Account, bal: Balance) -> dict[str, Any]:
    scale = acc.currency.scale
    return {
        "account_id": acc.id,
        "currency": acc.currency_id,
        "ledger": format_amount(bal.ledger, scale),
        "held": format_amount(bal.held, scale),
        "available": format_amount(bal.available, scale),
        "as_of": None,
    }


def historical_balance(acc: Account, ledger: Any, as_of: datetime) -> dict[str, Any]:
    return {
        "account_id": acc.id,
        "currency": acc.currency_id,
        "ledger": format_amount(ledger, acc.currency.scale),
        "held": None,
        "available": None,
        "as_of": as_of,
    }


def entries(items: Iterable[Entry], scales: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "id": e.id,
            "transaction_id": e.transaction_id,
            "account_id": e.account_id,
            "amount": format_amount(e.amount, scales[e.currency]),
            "currency": e.currency,
            "created_at": e.created_at,
        }
        for e in items
    ]


def transaction(txn: Transaction) -> dict[str, Any]:
    reversed_by = Transaction.objects.filter(reverses=txn).values_list("id", flat=True).first()
    return {
        "id": txn.id,
        "kind": txn.kind,
        "description": txn.description,
        "metadata": txn.metadata,
        "reverses": txn.reverses_id,
        "reversed_by": reversed_by,
        "created_at": txn.created_at,
        "entries": entries(txn.entries.order_by("id"), currency_scales()),
    }


def hold(h: Hold) -> dict[str, Any]:
    return {
        "id": h.id,
        "account_id": h.account_id,
        "amount": format_amount(h.amount, h.account.currency.scale),
        "currency": h.account.currency_id,
        "status": effective_status(h),
        "reason": h.reason,
        "expires_at": h.expires_at,
        "capture_transaction_id": h.capture_transaction_id,
        "created_at": h.created_at,
        "resolved_at": h.resolved_at,
    }
