# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-26

### Added

- `GET /v1/transactions`, newest first with a cursor, filterable by
  `account_id`, `kind`, `created_from` and `created_to`.
- `GET /v1/holds`, newest first with a cursor, filterable by `account_id` and
  `status`; an overdue hold counts as `expired`.
- `expire_holds` command: marks overdue holds `expired` and emits a
  `hold.expired` event once each. Balances were already correct without it.
- Webhook publisher: `LEDGER_OUTBOX_PUBLISHER=webhook` POSTs each outbox event
  to `LEDGER_WEBHOOK_URL`, signed with HMAC-SHA256 in `X-Ledger-Signature`
  (key `LEDGER_WEBHOOK_SECRET`), with the event id in `X-Ledger-Event-Id`.
  Non-2xx answers, timeouts and redirects are retried, never followed.

### Changed

- **License: MIT to AGPL-3.0-only.**
- Holds have a persisted `expired` status once swept; releasing an expired hold
  is still allowed. Migration `0005` only changes field choices.
- Docker base image: `python:3.15-slim`.
- Dependencies: Django 6.1.3, django-ninja 1.8.0, pydantic 2.14.3 with
  pydantic-core 2.50.4, asgiref 3.13.0, psycopg 3.3.8, sqlparse 0.6.1,
  typing-inspection 0.4.5, tzdata 2026.6, hypothesis 6.170.15, mypy 2.4.1,
  pytest 9.2.0 and ruff 0.17.4.

## [0.1.1] - 2026-10-15

### Fixed

- `POST /v1/holds` with an `expires_at` that has no UTC offset returned a 500;
  it is now refused with a 422 `validation_error`.

### Changed

- Dependencies: Django 6.1.2 (security fixes), pydantic 2.14.1 with
  pydantic-core 2.50.1, hypothesis 6.169.4 and ruff 0.17.0.

## [0.1.0] - 2026-09-30

### Added

- Double-entry accounts (asset, liability, equity, revenue, expense) with
  per-currency precision, and balances derived from append-only entries plus
  snapshots.
- Deposits, withdrawals, transfers and free-form balanced journal entries,
  all posted under row locks taken in account-id order.
- Holds that reserve funds and are captured (fully or partly), released or
  left to expire.
- Reversal transactions, at most one per original.
- Database triggers that reject updates and deletes of entries and
  transactions, and refuse to commit an unbalanced transaction.
- A mandatory `Idempotency-Key` on every write, with request-hash conflict
  detection and byte-exact response replay.
- RFC 9457 problem details with stable error codes, and opaque cursor
  pagination for entry history.
- A transactional outbox with an `outbox_worker` command that publishes to the
  log or a Redis stream, at least once.
- A `reconcile` command that runs eight invariant checks and exits non-zero on
  any discrepancy.
- `create_api_client` and `snapshot_balances` commands.
- Unit, API, Hypothesis property and real-Postgres concurrency tests.
- Docker image, `docker compose` stack, k6 benchmark, and CI through the
  shared `ci-workflows` pipelines.

[Unreleased]: https://github.com/CtrlAltDevelop/ledger-service/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/CtrlAltDevelop/ledger-service/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/CtrlAltDevelop/ledger-service/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/CtrlAltDevelop/ledger-service/releases/tag/v0.1.0
