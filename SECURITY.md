# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository instead, and include:

- what an attacker can do, and what they need to do it;
- steps or a request sequence that reproduces it;
- the commit or version you tested.

You should get an acknowledgement within a week. Fixes land on `main` and
are noted in the changelog once released.

## Supported versions

Only the latest release, and `main`, receive fixes.

## In scope

Anything that breaks one of the invariants in the README counts: creating or
destroying money, overdrawing a guarded account, posting twice under one
idempotency key, reading or moving another client's accounts, or editing
posted entries through the API.

## Deployment notes

The service expects to run behind a gateway that terminates TLS and rate
limits. It should connect to Postgres as a role that does not own the ledger
tables, so the append-only triggers cannot be disabled from the application.
API tokens are stored only as SHA-256 hashes; a leaked token is revoked by
setting its client's `is_active` to false.
