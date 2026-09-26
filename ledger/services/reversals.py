"""Reversals: the only way to undo a posted transaction.

The original is never touched. A new transaction with every leg negated is
posted against it, so history shows both what happened and that it was
undone.
"""

from uuid import UUID

from django.db import transaction

from ledger import errors
from ledger.models import ApiClient, Transaction, TransactionKind
from ledger.services.posting import Leg, post


def reverse(*, client: ApiClient, transaction_id: UUID, description: str = "") -> Transaction:
    with transaction.atomic():
        # Locking the original serialises concurrent attempts to reverse it,
        # so the loser sees the winner's reversal instead of a unique error.
        try:
            original = Transaction.objects.select_for_update().get(id=transaction_id, client=client)
        except Transaction.DoesNotExist:
            raise errors.NotFound(f"transaction {transaction_id} does not exist") from None

        if original.kind == TransactionKind.REVERSAL:
            raise errors.NotReversible("a reversal cannot itself be reversed")
        if Transaction.objects.filter(reverses=original).exists():
            raise errors.AlreadyReversed(f"transaction {original.id} has already been reversed")

        legs = [Leg(e.account_id, -e.amount, e.currency) for e in original.entries.all()]
        return post(
            client=client,
            kind=TransactionKind.REVERSAL,
            legs=legs,
            description=description or f"Reversal of {original.id}",
            reverses=original,
        )
