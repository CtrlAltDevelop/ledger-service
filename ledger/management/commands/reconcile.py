import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ledger.services.reconciliation import reconcile


class Command(BaseCommand):
    help = "Check every ledger invariant against the raw tables. Exits 1 on any discrepancy."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
        parser.add_argument(
            "--row-limit", type=int, default=100, help="Offending rows to show per check."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = reconcile(row_limit=options["row_limit"])
        if options["json"]:
            self.stdout.write(json.dumps(report.as_dict(), indent=2, default=str))
        else:
            for d in report.discrepancies:
                self.stdout.write(self.style.ERROR(f"{d.check}: {d.description}"))
                for row in d.rows:
                    self.stdout.write(f"  {row}")
            if report.ok:
                self.stdout.write(self.style.SUCCESS(f"{report.checks_run} checks, all clean"))
        if not report.ok:
            raise CommandError(f"{len(report.discrepancies)} check(s) failed")
