# 1. Derive balances from entries; never store them

- Status: accepted
- Date: 2026-09-26

## Context

The obvious schema puts a `balance` column on `accounts` and updates it on
every posting. It is fast to read and easy to explain. It is also a second
copy of the truth: the entries say one thing, the column says another, and
when they disagree (a bug, a partial write, a manual fix in psql) nothing
tells you which is right. Auditors, regulators and anyone reconciling against
a bank statement care about the entries, not the column.

A mutable balance also makes every posting `UPDATE` a hot row, and it makes
"what was the balance at 09:00 last Tuesday?" unanswerable without a separate
history table, which is the entries table again.

## Decision

`accounts` has no balance column. An account's balance is the sum of its
entries, and entries are append-only (ADR 0006).

To keep reads from summing an ever-growing history, the service writes
`balance_snapshots`: the raw sum of an account's entries up to a given entry
id. A balance is the latest snapshot plus the entries after it. Snapshots are
a cache in the strict sense: deleting every one of them changes no balance,
it only makes reads slower. ADR 0003 covers when they are taken and why they
cannot be wrong.

Amounts are signed: positive is a debit, negative a credit. Each account type
has a normal side (debit for assets and expenses, credit for the rest), and
the API reports balances in that direction, so a customer wallet (a
liability) with money in it shows a positive balance.

## Consequences

- There is one source of truth. `manage.py reconcile` re-derives every
  balance from it and checks every snapshot against it.
- Historical balances (`?as_of=`) come for free: sum the entries up to that
  time.
- A balance read costs a snapshot lookup plus an indexed scan of at most
  `LEDGER_SNAPSHOT_EVERY` rows, instead of one row read. That is the price,
  and it is bounded.
- Postings still lock the account row (ADR 0002). With no balance column,
  that lock protects no value on the row; it serialises the check-then-insert
  for everything that belongs to the account.
