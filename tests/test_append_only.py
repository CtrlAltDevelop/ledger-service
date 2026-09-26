"""The database itself refuses to edit history or post unbalanced money."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from ledger.models import Account, ApiClient, Entry, Transaction, TransactionKind
from tests.conftest import AccountFactory


def _post(client: ApiClient, legs: list[tuple[Account, str]]) -> Transaction:
    txn = Transaction.objects.create(client=client, kind=TransactionKind.JOURNAL)
    for account, amount in legs:
        Entry.objects.create(
            transaction=txn, account=account, amount=Decimal(amount), currency="USD"
        )
    return txn


def _run_deferred_checks() -> None:
    # Deferred triggers fire at COMMIT, which a test transaction never
    # reaches; ask for them now instead.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.fixture
def posted(api_client_record: ApiClient, make_account: AccountFactory) -> Transaction:
    a, b = make_account(), make_account()
    return _post(api_client_record, [(a, "10"), (b, "-10")])


def test_entries_cannot_be_updated(posted: Transaction) -> None:
    with pytest.raises(IntegrityError, match="append-only"), transaction.atomic():
        Entry.objects.filter(transaction=posted).update(amount=Decimal("99"))


def test_entries_cannot_be_deleted(posted: Transaction) -> None:
    with pytest.raises(IntegrityError, match="append-only"), transaction.atomic():
        Entry.objects.filter(transaction=posted).delete()


def test_transactions_cannot_be_updated(posted: Transaction) -> None:
    with pytest.raises(IntegrityError, match="append-only"), transaction.atomic():
        Transaction.objects.filter(pk=posted.pk).update(description="rewritten")


def test_unbalanced_transaction_is_rejected_at_commit(
    api_client_record: ApiClient, make_account: AccountFactory
) -> None:
    a, b = make_account(), make_account()

    def post_and_commit_check() -> None:
        with transaction.atomic():
            _post(api_client_record, [(a, "10"), (b, "-9")])
            _run_deferred_checks()

    with pytest.raises(IntegrityError, match="does not balance"):
        post_and_commit_check()


def test_balanced_transaction_passes_the_commit_check(posted: Transaction) -> None:
    _run_deferred_checks()

    assert posted.entries.count() == 2
