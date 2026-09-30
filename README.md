# Ledger Service

[![CI](https://github.com/CtrlAltDevelop/ledger-service/actions/workflows/ci.yml/badge.svg)](https://github.com/CtrlAltDevelop/ledger-service/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Django](https://img.shields.io/badge/django-5.2-green)
![PostgreSQL](https://img.shields.io/badge/postgresql-16-336791)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

A double-entry ledger over HTTP. It moves money between accounts
**atomically**, **idempotently** and **append-only**, so money is never
created or lost, whatever the concurrency, the retries or the crashes.

Python 3.12+, Django 5.2, Django Ninja, PostgreSQL 16, with Redis as an
optional event sink.

- Every movement is a transaction of two or more entries that sum to zero in
  every currency. The database refuses to commit one that does not.
- Balances are never stored. They are derived from entries, with snapshots to
  keep reads fast.
- Entries are never updated or deleted, and a trigger enforces it. Mistakes
  are undone with reversal transactions.
- Every write requires an `Idempotency-Key`. Retries replay the stored
  response, and a key reused with a different body is refused.
- Concurrent postings serialise on row locks taken in a fixed order: no
  overdrafts, no deadlocks, no retry loops.
- Events leave through a transactional outbox, at least once.
- A reconciliation job re-checks every invariant against the raw tables.

## Architecture

```mermaid
flowchart LR
    client([API client]) -- "Bearer token<br/>Idempotency-Key" --> api

    subgraph app [Django + Ninja]
        api[HTTP API<br/>ledger/api] --> idem[Idempotency<br/>claim + replay]
        idem --> services[Services<br/>payments, holds, reversals]
        services --> posting[posting.post<br/>lock, validate, write]
    end

    subgraph pg [PostgreSQL]
        accounts[(accounts)]
        entries[(entries<br/>append-only)]
        snapshots[(balance_snapshots)]
        holds[(holds)]
        idemtbl[(idempotency_records)]
        outbox[(outbox_events)]
    end

    posting -- "SELECT ... FOR UPDATE<br/>ORDER BY id" --> accounts
    posting -- "one transaction" --> entries & snapshots & outbox
    idem --> idemtbl
    services --> holds

    worker[outbox_worker] -- "FOR UPDATE SKIP LOCKED" --> outbox
    worker -- "XADD, at least once" --> redis[(Redis stream)]
    reconcile[reconcile] -. "read-only checks" .-> pg
```

A request goes API → idempotency → service → `posting.post`, and everything
from claiming the idempotency key to writing the outbox event commits in
**one** Postgres transaction. The worker and the reconciliation job are
separate processes that only ever talk to the database.

| Path | What lives there |
|---|---|
| `ledger/models.py` | Tables. No balance column anywhere. |
| `ledger/services/posting.py` | The only code that writes entries: lock, validate, check funds, write, snapshot, enqueue. |
| `ledger/services/` | Payments, holds, reversals, balances, outbox, reconciliation, API clients. |
| `ledger/api/` | Routes, schemas, RFC 9457 problems, idempotency, cursor pagination. |
| `ledger/migrations/0002_*` | The append-only and balance triggers. |
| `ledger/management/commands/` | `outbox_worker`, `reconcile`, `snapshot_balances`, `create_api_client`. |
| `docs/adr/` | Why it is built this way. |

## Invariants

What is always true, what enforces it, and the test that proves it.

| Invariant | Enforced by | Proven by |
|---|---|---|
| Every transaction's entries sum to zero in each currency | `posting._validate_legs`; deferred constraint trigger at `COMMIT` | `test_posting.py`, `test_append_only.py::test_unbalanced_transaction_is_rejected_at_commit` |
| All entries in a currency sum to zero: money is neither created nor destroyed | The rule above, for every transaction | `test_properties.py` (Hypothesis, random programs of every operation) |
| Entries and transactions are never updated or deleted | `BEFORE UPDATE OR DELETE` triggers | `test_append_only.py` |
| An account that may not overdraw never goes below zero, counting holds | Row locks in id order + funds check after the lock | `test_concurrency.py::test_a_hundred_concurrent_withdrawals_never_overdraw` (100 racing withdrawals from a balance of 10 → exactly 10 succeed), `test_properties.py` |
| Concurrent postings never deadlock | Locks always taken in ascending account id | `test_concurrency.py::test_opposing_transfers_do_not_deadlock` |
| One idempotency key, one effect | Key claimed with `INSERT ... ON CONFLICT` in the operation's transaction | `test_concurrency.py::test_fifty_identical_requests_post_one_transaction`, `test_idempotency.py` |
| A snapshot equals the sum of the entries it covers | Snapshots taken only under the account lock | `test_posting.py`, `test_properties.py`, reconciliation's `snapshot_drift` |
| A transaction is reversed at most once | Unique `reverses_id` + lock on the original | `test_concurrency.py::test_a_transaction_is_reversed_at_most_once_under_contention` |
| Every posted change has exactly one event, published at least once | Outbox row in the same transaction; mark published after the broker accepts | `test_outbox.py`, `test_concurrency.py::test_parallel_outbox_workers_publish_each_event_once` |
| Amounts are exact and never finer than the currency allows | `Decimal` in a 38-digit context, `numeric(38,18)`, per-currency scale check | `test_money.py`, `test_api.py` |
| Anything that slips past the rules above is reported | `manage.py reconcile`: 8 checks, exits 1 on any failure | `test_reconciliation.py` (corrupts the tables with triggers disabled) |

## API

Every route but `/healthz` needs `Authorization: Bearer <token>`. Every
`POST` needs an `Idempotency-Key` header (1–255 visible ASCII characters; a
UUID is ideal). OpenAPI docs are served at `/docs`.

| Method | Path | Does |
|---|---|---|
| `POST` | `/v1/accounts` | Open an account: `owner_id`, `currency`, `type` (`asset`, `liability`, `equity`, `revenue`, `expense`), `allow_overdraft` |
| `GET` | `/v1/accounts/{id}` | Read an account |
| `PATCH` | `/v1/accounts/{id}` | Freeze or unfreeze (`status`). A frozen account can receive but not send. |
| `GET` | `/v1/accounts/{id}/balance` | `ledger`, `held`, `available`; with `?as_of=<ISO 8601>`, the ledger balance at that time |
| `GET` | `/v1/accounts/{id}/entries` | Entries, newest first, paged with `?cursor=&limit=` |
| `POST` | `/v1/deposits` | Money in from outside: `account_id`, `amount`, `currency` |
| `POST` | `/v1/withdrawals` | Money out to outside |
| `POST` | `/v1/transfers` | `from` → `to`, same currency and same side of the ledger |
| `POST` | `/v1/transactions` | Any balanced set of 2–50 signed entries, e.g. a trade with a fee leg |
| `GET` | `/v1/transactions/{id}` | A transaction and its entries |
| `POST` | `/v1/transactions/{id}/reverse` | Post the exact negation, linked to the original |
| `POST` | `/v1/holds` | Reserve funds: `account_id`, `amount`, `currency`, optional `expires_at` |
| `GET` | `/v1/holds/{id}` | Read a hold: `active`, `captured`, `released` or `expired` |
| `POST` | `/v1/holds/{id}/capture` | Move up to the held amount to `to`; any remainder is released |
| `POST` | `/v1/holds/{id}/release` | Let the funds go |

```http
POST /v1/transfers
Authorization: Bearer lsk_...
Idempotency-Key: 5b3c7e2a-7a0e-4b8e-9f55-1d1c1f1e2a10
Content-Type: application/json

{"from": "…", "to": "…", "amount": "12.50", "currency": "USD"}
```

**Money.** Amounts travel as decimal strings (`"12.50"`), never JSON numbers,
and must fit the currency's minor unit: `"1.001"` USD is refused, not
rounded. Storage is `numeric(38,18)`, and Python arithmetic runs in a
38-digit decimal context. Entry amounts are signed, positive for debits and
negative for credits; balances are reported in the account's normal
direction, so a funded customer wallet (a liability) shows a positive
balance.

**Deposits and withdrawals** post against a per-client, per-currency
settlement account (an asset that may go negative), created on first use. The
real bank or chain behind it is out of scope.

**Errors** are [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem
details (`application/problem+json`) with a stable `code`:

```json
{
  "type": "urn:ledger:problem:insufficient_funds",
  "title": "Insufficient funds",
  "status": 422,
  "code": "insufficient_funds",
  "detail": "account 3f0… has insufficient available funds",
  "account_id": "3f0…"
}
```

| `code` | Status |
|---|---|
| `validation_error`, `invalid_amount`, `unknown_currency`, `currency_mismatch`, `unbalanced_transaction`, `incompatible_accounts`, `insufficient_funds`, `invalid_expiry` | 422 |
| `idempotency_conflict`, `already_reversed`, `not_reversible`, `hold_not_active`, `account_inactive` | 409 |
| `idempotency_key_required`, `invalid_cursor` | 400 |
| `unauthenticated` | 401 |
| `not_found` | 404 |

**Idempotency.** A retry with the same key and body gets the stored
response, byte for byte, with `Idempotent-Replayed: true`. That includes
stored errors: a withdrawal refused for insufficient funds stays refused
under that key. Use a new key for a new attempt. The same key with a
different body, or on another endpoint, is `409 idempotency_conflict`.

**Events.** `transaction.posted`, `account.created`, `hold.created`,
`hold.captured` and `hold.released`, each with a stable `id` to deduplicate
on, because delivery is at least once.

## Quickstart

With Docker:

```sh
cp .env.example .env
docker compose up -d --build          # api on :8000, worker, Postgres, Redis
TOKEN=$(docker compose exec -T api python manage.py create_api_client demo)

curl -s localhost:8000/v1/accounts \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' \
  -d '{"owner_id": "alice", "currency": "USD"}'
```

Locally, with [uv](https://docs.astral.sh/uv/) and a Postgres you already
have:

```sh
make install
export DATABASE_URL=postgres://postgres:postgres@localhost:5432/ledger
export DJANGO_SECRET_KEY=dev DJANGO_DEBUG=true
make migrate
make run           # API on :8000
make worker        # in another shell
make check         # ruff, mypy --strict, pytest
```

### Operations

| Command | When |
|---|---|
| `manage.py outbox_worker [--once]` | Always running. Handles SIGTERM by finishing the batch in hand. |
| `manage.py reconcile [--json]` | On a schedule, e.g. nightly. Exits 1 on any discrepancy, so alert on the exit code. |
| `manage.py snapshot_balances [--min-entries N]` | Optional, off-peak. Busy accounts are snapshotted inline every `LEDGER_SNAPSHOT_EVERY` entries. |
| `manage.py create_api_client NAME` | Prints a new client's token once; only its SHA-256 is stored. |

## Tests

```sh
make test
```

113 tests: unit, API, property-based (Hypothesis) and concurrency. They need a
real PostgreSQL, because the guarantees under test are Postgres behaviour:
triggers, deferred constraints, `FOR UPDATE`, `SKIP LOCKED`, unique-index
waits. The concurrency tests commit for real and race 40 threads, each with
its own connection. That stays under Postgres' default `max_connections`, so
CI runs them against a stock `postgres:16` service container.

## Benchmark

Measured with [`bench/transfers.js`](bench/transfers.js) (k6) against the
`docker compose` stack: 4 gunicorn sync workers, 20 virtual users, 30 s per
run, every request with a fresh idempotency key. The host was a laptop with
an Intel Core i7-7700HQ (4 cores, 8 threads) and 32 GB RAM, running Docker
Desktop on WSL2 (8 CPUs, 15.5 GiB available to containers), with Postgres
16 on a Docker volume.

| Scenario | Transfers/s | p50 | p95 | p99 |
|---|---:|---:|---:|---:|
| `spread`: each user between its own accounts, no lock contention | 78 | 242 ms | 364 ms | 415 ms |
| `hot`: every user pays into one shared account | 35 | 489 ms | 1.08 s | 2.42 s |

A single user on the uncontended path saw p50 36 ms and p95 59 ms, which is
about 20 round trips to Postgres per transfer: idempotency claim, locks,
balance and hold reads, inserts, savepoints and the response read-back.
Throughput did not change when the worker count went from 4 to 8, so on this
machine the ceiling is Postgres commit latency under Docker Desktop, not
Python. Repeated runs varied by about ±30%: one `spread` run gave 52/s.
Treat these numbers as the shape of the trade-off, not a capacity figure. The
hot account halves throughput because every posting queues on one row lock
(ADR 0002). After the runs, about 10,500 transactions, `reconcile` was clean
and every outbox event had reached the Redis stream.

To reproduce, see [`bench/README.md`](bench/README.md).

## Known limitations

- **Hot accounts serialise.** Every posting that touches an account holds its
  row lock. A fee or treasury account that every transfer touches caps
  throughput, as the benchmark shows. The fixes, sharding it into
  sub-accounts or batching postings into it, are not built.
- **No currency conversion.** A transaction can carry several currencies, but
  each must balance on its own. There are no FX quotes or locked rates, and
  so no policy for rounding remainders.
- **No maker-checker approvals** for large withdrawals.
- **Idempotency records and published outbox rows are kept forever.** Pruning
  them is a scheduled `DELETE` nobody has written yet.
- **`as_of` balances use the app server's clock**, the transaction timestamp
  set at posting time. Several app servers with skewed clocks can disagree
  about which side of a cut-off a transaction fell on.
- **Authentication is static bearer tokens**, with no scopes, rotation or rate
  limiting. Put the service behind a gateway that handles those.
- **Triggers do not stop `TRUNCATE` or a superuser.** Run the app as a role
  that does not own the tables, and rely on `reconcile` to catch the rest.
- **Deposits and withdrawals do not talk to a bank.** They post against a
  settlement account, and the gateway is out of scope.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) © 2026 Mohammad Zarif
