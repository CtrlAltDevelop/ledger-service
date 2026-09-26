"""Reconciliation: re-derive every invariant from the raw tables.

Posting enforces these rules as money moves; this module checks after the
fact that they still hold, whatever wrote to the database since. It reads
only, and reports every discrepancy rather than stopping at the first.

Run it on a schedule (``manage.py reconcile``) and alert on a non-zero exit.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from django.db import connection, transaction

# Each check is one query returning the offending rows; no rows means healthy.
CHECKS: dict[str, tuple[str, str]] = {
    "unbalanced_transaction": (
        "A transaction's entries do not sum to zero in some currency.",
        """
        SELECT transaction_id::text, currency, SUM(amount)::text
          FROM ledger_entry
         GROUP BY transaction_id, currency
        HAVING SUM(amount) <> 0
        """,
    ),
    "ledger_imbalance": (
        "All entries in a currency do not sum to zero: money was created or destroyed.",
        """
        SELECT currency, SUM(amount)::text
          FROM ledger_entry
         GROUP BY currency
        HAVING SUM(amount) <> 0
        """,
    ),
    "too_few_entries": (
        "A transaction has fewer than two entries.",
        """
        SELECT t.id::text, COUNT(e.id)
          FROM ledger_transaction t
          LEFT JOIN ledger_entry e ON e.transaction_id = t.id
         GROUP BY t.id
        HAVING COUNT(e.id) < 2
        """,
    ),
    "entry_currency_mismatch": (
        "An entry's currency differs from its account's.",
        """
        SELECT e.id, e.currency, a.currency
          FROM ledger_entry e
          JOIN ledger_account a ON a.id = e.account_id
         WHERE e.currency <> a.currency
        """,
    ),
    "entry_precision": (
        "An entry has more decimal places than its currency allows.",
        """
        SELECT e.id, e.amount::text, c.scale
          FROM ledger_entry e
          JOIN ledger_currency c ON c.code = e.currency
         WHERE e.amount <> ROUND(e.amount, c.scale)
        """,
    ),
    "snapshot_drift": (
        "A balance snapshot disagrees with the entries it summarises.",
        """
        SELECT s.id, s.account_id::text, s.balance::text,
               COALESCE(SUM(e.amount), 0)::text
          FROM ledger_balancesnapshot s
          LEFT JOIN ledger_entry e
                 ON e.account_id = s.account_id AND e.id <= s.as_of_entry_id
         GROUP BY s.id, s.account_id, s.balance
        HAVING s.balance <> COALESCE(SUM(e.amount), 0)
        """,
    ),
    "overdrawn_account": (
        "An account that may not overdraw has a negative balance.",
        """
        SELECT a.id::text, SUM(e.amount)::text, a.type
          FROM ledger_account a
          JOIN ledger_entry e ON e.account_id = a.id
         WHERE NOT a.allow_overdraft
         GROUP BY a.id, a.type
        HAVING SUM(e.amount)
               * CASE WHEN a.type IN ('asset', 'expense') THEN 1 ELSE -1 END < 0
        """,
    ),
    "hold_capture_mismatch": (
        "A captured hold's transaction moved more than the hold reserved.",
        """
        SELECT h.id::text, h.amount::text, SUM(e.amount)::text
          FROM ledger_hold h
          JOIN ledger_entry e
            ON e.transaction_id = h.capture_transaction_id AND e.account_id = h.account_id
         WHERE h.status = 'captured'
         GROUP BY h.id, h.amount
        HAVING ABS(SUM(e.amount)) > h.amount
        """,
    ),
}


@dataclass(frozen=True, slots=True)
class Discrepancy:
    check: str
    description: str
    rows: list[list[Any]]


@dataclass(slots=True)
class Report:
    discrepancies: list[Discrepancy] = field(default_factory=list)
    checks_run: int = 0

    @property
    def ok(self) -> bool:
        return not self.discrepancies

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks_run": self.checks_run,
            "discrepancies": [asdict(d) for d in self.discrepancies],
        }


def reconcile(*, row_limit: int = 100) -> Report:
    """Run every check and report what failed, with up to ``row_limit`` rows each."""
    report = Report()
    outermost = not connection.in_atomic_block
    with transaction.atomic(), connection.cursor() as cursor:
        if outermost:
            # One snapshot for every check, so rows committed mid-run cannot
            # make two checks disagree with each other.
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for name, (description, sql) in CHECKS.items():
            cursor.execute(f"{sql} LIMIT %s", [row_limit])
            rows = [list(row) for row in cursor.fetchall()]
            report.checks_run += 1
            if rows:
                report.discrepancies.append(Discrepancy(name, description, rows))
    return report
