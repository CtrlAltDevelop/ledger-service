import json
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.backends.utils import CursorWrapper

from ledger.models import (
    Account,
    ApiClient,
    BalanceSnapshot,
    Entry,
    Transaction,
    TransactionKind,
)
from ledger.services import balances, payments
from ledger.services.reconciliation import reconcile
from tests.conftest import AccountFactory, fund


@pytest.fixture
def busy_ledger(api_client_record: ApiClient, make_account: AccountFactory) -> Account:
    alice, bob = make_account(), make_account()
    for amount in ("10", "20", "30"):
        fund(alice, amount)
    payments.transfer(
        client=api_client_record,
        source_id=alice.id,
        destination_id=bob.id,
        amount=Decimal("15"),
        currency="USD",
    )
    balances.take_snapshot(alice)
    return alice


@contextmanager
def ledger_triggers_disabled() -> Iterator[CursorWrapper]:
    """What an operator with table-owner rights can still do by hand."""
    with connection.cursor() as cursor:
        # Postgres will not alter a table with trigger events pending, so run
        # the deferred balance checks queued by earlier postings first.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE ledger_entry DISABLE TRIGGER USER")
        yield cursor
        cursor.execute("ALTER TABLE ledger_entry ENABLE TRIGGER USER")


def failed_checks() -> set[str]:
    return {d.check for d in reconcile().discrepancies}


def test_a_healthy_ledger_reconciles(busy_ledger: Account) -> None:
    report = reconcile()

    assert report.ok
    assert report.checks_run == 8


def test_an_entry_edited_behind_the_ledgers_back_is_reported(busy_ledger: Account) -> None:
    entry = Entry.objects.filter(account=busy_ledger).order_by("id").first()
    assert entry is not None
    with ledger_triggers_disabled() as cursor:
        cursor.execute("UPDATE ledger_entry SET amount = amount - 1 WHERE id = %s", [entry.id])

    assert failed_checks() == {"unbalanced_transaction", "ledger_imbalance", "snapshot_drift"}


def test_a_one_legged_transaction_is_reported(
    api_client_record: ApiClient, busy_ledger: Account
) -> None:
    txn = Transaction.objects.create(client=api_client_record, kind=TransactionKind.JOURNAL)
    with ledger_triggers_disabled():
        Entry.objects.create(
            transaction=txn, account=busy_ledger, amount=Decimal(-5), currency="USD"
        )

    assert failed_checks() == {"unbalanced_transaction", "ledger_imbalance", "too_few_entries"}


def test_a_drifted_snapshot_is_reported(busy_ledger: Account) -> None:
    last = Entry.objects.filter(account=busy_ledger).latest("id")
    BalanceSnapshot.objects.create(
        account=busy_ledger, as_of_entry_id=last.id + 1, balance=Decimal("-1000000")
    )

    assert failed_checks() == {"snapshot_drift"}


def test_an_overdrawn_account_is_reported(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    wallet, cash = make_account(), make_account(allow_overdraft=True)
    txn = Transaction.objects.create(client=api_client_record, kind=TransactionKind.JOURNAL)
    Entry.objects.bulk_create(
        [
            Entry(transaction=txn, account=wallet, amount=Decimal(5), currency="USD"),
            Entry(transaction=txn, account=cash, amount=Decimal(-5), currency="USD"),
        ]
    )

    assert failed_checks() == {"overdrawn_account"}


def test_an_entry_finer_than_its_currency_is_reported(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    a, b = make_account(allow_overdraft=True), make_account()
    txn = Transaction.objects.create(client=api_client_record, kind=TransactionKind.JOURNAL)
    Entry.objects.bulk_create(
        [
            Entry(transaction=txn, account=a, amount=Decimal("0.001"), currency="USD"),
            Entry(transaction=txn, account=b, amount=Decimal("-0.001"), currency="USD"),
        ]
    )

    assert failed_checks() == {"entry_precision"}


def test_the_command_exits_non_zero_on_a_discrepancy(busy_ledger: Account) -> None:
    last = Entry.objects.filter(account=busy_ledger).latest("id")
    BalanceSnapshot.objects.create(account=busy_ledger, as_of_entry_id=last.id + 1, balance=1)
    out = StringIO()

    with pytest.raises(CommandError, match="1 check"):
        call_command("reconcile", "--json", stdout=out)

    report = json.loads(out.getvalue())
    assert report["ok"] is False
    assert report["discrepancies"][0]["check"] == "snapshot_drift"


def test_the_command_reports_a_clean_ledger(busy_ledger: Account) -> None:
    out = StringIO()

    call_command("reconcile", stdout=out)

    assert "8 checks, all clean" in out.getvalue()
