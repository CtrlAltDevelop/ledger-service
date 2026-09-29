# 4. Claim idempotency keys in the same transaction as the operation

- Status: accepted
- Date: 2026-09-26

## Context

Clients retry. A timeout does not say whether the transfer happened, and a
mobile client on a train will send the same request three times. So every
write requires an `Idempotency-Key`, and a repeated key must return the
original outcome without doing the work again.

The common design puts the keys in a separate store, often Redis: check the
key, run the operation, save the response. That is two systems and three
steps, and it fails in the gaps between them. Crash after the operation but
before saving the response, and the retry moves the money twice. Save an
"in progress" marker first, and a crash leaves the key stuck until someone
expires it.

A key can also come back with a different body, through a bug or a client
that reuses keys. Replaying the first response would tell the client its
second request succeeded when it never ran.

## Decision

The key is claimed in Postgres, in the same database transaction as the
operation (`ledger/api/idempotency.py`):

1. `INSERT INTO ledger_idempotencyrecord ... ON CONFLICT (client_id, key) DO
   NOTHING RETURNING id`.
2. If that claimed the key, run the operation in a savepoint, store the
   response status and exact body on the row, and commit it all together.
3. If the key already existed, compare the stored SHA-256 of method, path and
   canonical JSON body. A match replays the stored response with
   `Idempotent-Replayed: true`. A mismatch is `409 idempotency_conflict`.

A concurrent duplicate blocks on the unique index in step 1 until the first
attempt finishes. If the first attempt commits, the duplicate finds the row
and replays it. If it rolls back, the duplicate's insert goes through and it
runs as the first attempt. There is no "in progress" state and nothing to
expire.

Domain errors such as `insufficient_funds` roll back the savepoint, but they
are stored and replayed: a retry is the same request, so it gets the same
answer. Schema validation happens before the key is claimed, and an
unexpected exception rolls the claim back, so neither a malformed request nor
a crash uses up the key.

## Consequences

- One transaction per key. `tests/test_concurrency.py` proves it: fifty
  simultaneous identical requests produce one deposit and fifty byte-identical
  201 responses.
- The stored body is the exact text sent the first time, not jsonb. jsonb
  reorders keys, and the concurrency test caught replays that differed from
  the original byte-wise.
- A request that failed with `insufficient_funds` keeps failing under the
  same key even after the account is funded. A new attempt needs a new key,
  which is Stripe's contract too.
- Keys are scoped to the API client and never expire. A retention job that
  prunes old records would be easy to add.
