# 3. Take balance snapshots under the account lock

- Status: accepted
- Date: 2026-09-26

## Context

A snapshot says "the entries of account X with id up to N sum to B". The
balance is then B plus the entries of X with an id greater than N. That is
only correct if no entry of X with an id at most N can appear after the
snapshot is taken.

Sequence ids are handed out at insert time, not at commit time. Transaction
T1 can insert entry 100, transaction T2 can insert entry 101 and commit, and
a snapshot taken at that moment sees 101 but not 100. It records N = 101, and
when T1 commits, entry 100 sits behind the snapshot for good. The balance is
silently wrong by that amount.

## Decision

Snapshots are only taken while holding the account's row lock:

- inline, at the end of a posting, once an account has `LEDGER_SNAPSHOT_EVERY`
  entries since its last snapshot. The posting already holds the lock and has
  just computed the balance;
- by `manage.py snapshot_balances`, which locks each account in its own short
  transaction, for accounts too quiet to reach the threshold.

Every posting locks an account before inserting entries for it (ADR 0002).
While a snapshot holds the lock, no transaction can be halfway through
inserting entries for that account: every entry with a lower id has already
committed, and every later one will be inserted after the lock is released,
with a higher id. For any one account, id order is commit order.

## Consequences

- A snapshot cannot miss an entry. This holds by construction, not by timing.
- `reconcile` checks it anyway: `snapshot_drift` recomputes every snapshot
  from the entries it claims to cover.
- The posting that crosses the threshold does one extra insert. At the
  default of 500 that is one insert per 500 postings on the account.
- Historical `as_of` balances do not use snapshots. They sum entries by
  timestamp, because snapshots are keyed by entry id, not by time.
