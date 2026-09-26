from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from ledger.models import Account
from ledger.services.balances import take_snapshot


class Command(BaseCommand):
    help = (
        "Snapshot every account with enough entries since its last snapshot. "
        "Postings already snapshot busy accounts as they go; this catches the quiet ones."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--min-entries",
            type=int,
            default=1,
            help="Skip accounts with fewer new entries than this.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        taken = 0
        # One short transaction per account, so no lock is held for long.
        for account in Account.objects.order_by("id").iterator():
            if take_snapshot(account, min_entries=options["min_entries"]) is not None:
                taken += 1
        self.stdout.write(f"took {taken} snapshot(s)")
