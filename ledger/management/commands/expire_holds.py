from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from ledger.services import holds


class Command(BaseCommand):
    help = "Mark overdue holds expired and announce them. Safe to run from cron."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size: int = options["batch_size"]
        total = 0
        while (done := holds.expire_due(batch_size=batch_size)) > 0:
            total += done
        self.stdout.write(f"expired {total} hold(s)")
