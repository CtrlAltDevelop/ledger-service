# 5. Publish events through a transactional outbox, at least once

- Status: accepted
- Date: 2026-09-26

## Context

Other systems want to know when money moves: notifications, analytics, a
risk engine. Publishing to a broker from inside the request is a dual write.
Publish before commit, and the transaction may roll back, leaving an event
for a transfer that never happened. Publish after commit, and the process may
die in between, leaving a transfer nobody hears about. Neither order of the
two calls is safe, because they go to two systems that share no transaction.

## Decision

Every posting writes an `OutboxEvent` row (`transaction.posted`) in the same
transaction as its entries. Holds and accounts emit their own events the same
way. The event exists if and only if the change does.

A separate process, `manage.py outbox_worker`, publishes them:

- It claims a batch with `SELECT ... FOR UPDATE SKIP LOCKED`, oldest first,
  so several workers can run without taking the same event at once.
- It sets `published_at` only after the publisher returns. A crash in between
  leaves the event unpublished, so it goes out again: at least once, not
  exactly once.
- On the first failure it records the error and stops the batch, so a broker
  outage does not reorder events.

Publishers are pluggable. `log` writes JSON to the log; `redis` appends to a
Redis stream, which is what `docker compose` runs.

## Consequences

- No lost events and no phantom events, whatever crashes when.
  `tests/test_outbox.py` simulates a crash after the broker accepted an event
  and shows it is delivered again.
- Consumers must deduplicate. Every message carries the outbox row's `id`,
  which stays the same across redeliveries.
- Events lag by up to the worker's poll interval (1 s by default).
  LISTEN/NOTIFY could shorten that; nothing has needed it yet.
- Order is by creation time, and strict only with one worker. With several,
  events for different aggregates can interleave.
- The table grows. Published rows can be pruned on a schedule.
