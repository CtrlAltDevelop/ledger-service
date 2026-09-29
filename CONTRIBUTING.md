# Contributing

Thanks for taking the time. Bug reports, questions and pull requests are all
welcome.

## Setup

You need [uv](https://docs.astral.sh/uv/) and a PostgreSQL 16. The tests
depend on Postgres behaviour (triggers, row locks, `SKIP LOCKED`), so there is
no SQLite fallback.

```sh
make install
docker run -d --name ledger-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
export DATABASE_URL=postgres://postgres:postgres@localhost:5432/ledger
make check            # ruff, ruff format --check, mypy --strict, pytest
```

## Making a change

- Money moves only through `ledger.services.posting.post`. A new operation
  turns into legs and calls it; it does not write entries itself.
- Never add code that updates or deletes an entry or a transaction. The
  database will refuse it anyway. Corrections are reversals.
- A new write endpoint gets `@idempotent` and `openapi_extra=OPENAPI_EXTRA`,
  like the existing ones.
- A new client-facing error is a `LedgerError` subclass with a stable `code`.
  Codes are part of the API; do not rename them.
- If the change touches locking, add or extend a test in
  `tests/test_concurrency.py` that races it against a real database.
- A decision someone will later ask "why?" about gets an ADR in `docs/adr/`.
- Changing dependencies means `uv lock`, then `make requirements`, and
  committing both files.

## Commits and pull requests

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `build:`, `ci:`, `chore:`.
Write the subject as a short imperative that says what changed, e.g.
`fix: replay idempotent responses byte for byte`. Use the body for why.

Keep a pull request to one concern, add an entry under `[Unreleased]` in
`CHANGELOG.md`, and make sure CI is green.
