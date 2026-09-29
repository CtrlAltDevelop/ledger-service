# Benchmark

`transfers.js` is a [k6](https://k6.io) script that hammers `POST /v1/transfers`
in one of two shapes:

| `SCENARIO` | What it measures |
|---|---|
| `spread` (default) | Each virtual user moves money between its own pair of accounts. No two requests want the same row lock, so this is the uncontended ceiling. |
| `hot` | Every virtual user pays into one shared account. Every request queues on that account's lock, which is what a fee or treasury account sees in production. |

## Run it

```sh
docker compose up -d --build
export TOKEN=$(docker compose exec -T api python manage.py create_api_client bench)
SCENARIO=spread VUS=20 DURATION=30s make bench
SCENARIO=hot    VUS=20 DURATION=30s make bench
docker compose exec api python manage.py reconcile   # still clean afterwards
```

`setup()` opens one funded account pair per virtual user, plus the hot
account, before the clock starts. Every request carries a fresh
`Idempotency-Key`, so the cost of the idempotency claim is part of the
number. Treat transfers per second as `checks_succeeded / DURATION`.
`http_reqs` also counts the setup requests.
