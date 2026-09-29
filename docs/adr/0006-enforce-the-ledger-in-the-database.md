# 6. Enforce append-only, balanced postings in the database

- Status: accepted
- Date: 2026-09-26

## Context

The service layer never updates or deletes an entry, and never posts a
transaction whose entries fail to sum to zero. But the service is not the
only thing with a database connection. A data-fix script, a migration or an
operator in psql can do either, and a rule in application code cannot stop
them.

## Decision

Migration `0002_append_only_ledger` installs three triggers:

- `ledger_entry_append_only` and `ledger_transaction_append_only` reject any
  `UPDATE` or `DELETE` on those tables, with a hint to post a reversal
  instead.
- `ledger_entry_balanced`, a `DEFERRABLE INITIALLY DEFERRED` constraint
  trigger, checks at `COMMIT` that every transaction touched sums to zero in
  every currency. Deferring the check lets a posting insert its legs one at a
  time.

Mistakes are fixed with a reversal: a new transaction with every leg negated,
linked to the original by a unique `reverses_id`, so a transaction can be
reversed only once.

## Consequences

- Rewriting history now takes an explicit `ALTER TABLE ... DISABLE TRIGGER`.
  That needs table ownership and shows up in any audit of DDL.
- `TRUNCATE` is not blocked, because Django's test runner flushes tables with
  it. In production the application role should not own the tables, and
  should have only `SELECT` and `INSERT` on the entry and transaction tables.
- The balance trigger runs one small aggregate per inserted entry at commit.
- Reconciliation still re-checks everything, because a superuser can bypass
  triggers. `tests/test_reconciliation.py` does exactly that, and shows the
  damage is reported.
