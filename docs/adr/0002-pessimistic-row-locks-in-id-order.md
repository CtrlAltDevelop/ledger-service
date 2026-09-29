# 2. Serialise postings with row locks taken in id order

- Status: accepted
- Date: 2026-09-26

## Context

A withdrawal has to read the balance, check it covers the amount and insert
entries. Two concurrent withdrawals that both read "10" and both take 10
overdraw the account, and neither statement did anything wrong on its own.
Postgres' default READ COMMITTED isolation does not prevent this: each
statement sees a consistent snapshot, and one transaction's insert does not
conflict with the other's.

The options:

1. **SERIALIZABLE isolation.** Postgres detects the read/write dependency and
   aborts one transaction with a serialization failure. Correct, but every
   caller needs a retry loop, the abort rate climbs steeply on a busy
   account, and SSI's predicate locks are hard to reason about when tuning.
2. **Optimistic concurrency.** A version column on the account, bumped on
   every posting with `UPDATE ... WHERE version = n`. Cheap when conflicts are
   rare. On a hot account (fees, treasury) conflicts are the common case, and
   throughput collapses into retries.
3. **Pessimistic locks.** `SELECT ... FOR UPDATE` on every account a posting
   touches, before reading any balance. Postings on the same account queue;
   everything else runs in parallel.

## Decision

Option 3, under READ COMMITTED. `ledger.services.posting.lock_accounts` locks
the accounts involved with `SELECT ... FOR UPDATE ... ORDER BY id`, and only
then reads balances and holds. Under READ COMMITTED each statement after the
lock sees every transaction that committed before it, so the balance read is
current.

Locks are always taken in ascending account id. Otherwise transfers A to B
and B to A could each hold one lock and wait for the other, and Postgres
would abort one as a deadlock. Sorting makes the wait order total, so
contention means queueing, never deadlock. Holds follow the same order:
accounts first, then the hold row.

## Consequences

- No retry logic anywhere, in the service or its clients.
- `tests/test_concurrency.py` proves it against a real Postgres: 100
  withdrawals of 1 from a balance of 10 give exactly 10 successes, and 200
  opposing transfers between two accounts all succeed with no deadlock.
- A hot account serialises: every posting that touches it waits its turn. The
  benchmark's `hot` scenario shows the cost. The usual remedies, splitting it
  into sub-accounts or batching postings, are listed as known limitations in
  the README, not built.
- The lock is held for the whole posting transaction, so that transaction
  must stay short. It does no network I/O: events leave through the outbox
  (ADR 0005).
