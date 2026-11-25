# AGENTS.md — ledger-service

Canonical instructions for any AI agent or human working in this repo.
`CLAUDE.md` is a pointer only; new rules go **here**.

A double-entry ledger over HTTP (Django 6.1, Django Ninja, PostgreSQL 16, Redis as an optional event sink).
It moves money atomically, idempotently and append-only. Read `README.md`, `CONTRIBUTING.md` and `docs/adr/` first.

## Commands (`make help` lists all)

- `make check` — lint, mypy strict, tests: exactly what CI runs. Tests need PostgreSQL (triggers, row locks, `SKIP LOCKED`); there is no SQLite fallback.
- `make reconcile` — checks every ledger invariant against the database.
- `make requirements` — re-export the hash-pinned `requirements.txt` after any `uv.lock` change; CI and Docker install from it.

## Rules

- Money is never created or lost: every transfer is balanced entries inside one transaction. Amounts go through `ledger/money.py`, never floats.
- Entries are append-only. Corrections are new reversing entries, never updates or deletes.
- Every write endpoint is idempotent by key; a retry must return the first result.
- Schema changes ship as a migration; invariants that matter live in the database (triggers, constraints), not only in Python.
- CI workflows are reusable ones from `CtrlAltDevelop/ci-workflows`; change checks there, not here.
