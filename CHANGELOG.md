# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - Unreleased

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

[Unreleased]: https://github.com/CtrlAltDevelop/ledger-service/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CtrlAltDevelop/ledger-service/releases/tag/v0.1.0
