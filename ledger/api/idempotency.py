"""Mandatory Idempotency-Key on every POST.

The key is claimed and the operation runs inside one database transaction:

* ``INSERT ... ON CONFLICT DO NOTHING`` claims ``(client, key)``. A concurrent
  request with the same key blocks on the unique index until the first one
  commits, then finds the row and replays its response. If the first one
  rolls back, the second claims the key and runs as if it came first.
* The operation runs in a savepoint. A domain error (insufficient funds, say)
  rolls back the operation's writes but is itself stored and replayed, so a
  retry gets the same answer rather than a different one.
* A key reused with a different body is refused with ``idempotency_conflict``
  rather than silently returning a response to a request never made.

Unexpected exceptions roll the claim back too, so the client may retry.
See docs/adr/0004.
"""

import functools
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, Concatenate, cast

from django.db import connection, transaction
from django.http import HttpRequest, HttpResponse
from ninja import Status
from ninja.responses import NinjaJSONEncoder

from ledger import errors
from ledger.api import problems
from ledger.api.auth import client_of
from ledger.models import ApiClient, IdempotencyRecord

HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"
_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,255}$")  # visible ASCII, no spaces

_CLAIM_SQL = """
INSERT INTO ledger_idempotencyrecord (client_id, key, request_hash, response_body, created_at)
VALUES (%s, %s, %s, '', now())
ON CONFLICT (client_id, key) DO NOTHING
RETURNING id
"""

# Documents the header in the OpenAPI schema; the decorator enforces it.
OPENAPI_EXTRA: dict[str, Any] = {
    "parameters": [
        {
            "in": "header",
            "name": HEADER,
            "required": True,
            "description": "Unique per logical operation. Retries must reuse it.",
            "schema": {"type": "string", "maxLength": 255},
        }
    ]
}


def fingerprint(request: HttpRequest) -> str:
    """SHA-256 over method, path and the body with keys sorted.

    Canonicalising means ``{"a":1,"b":2}`` and ``{"b": 2, "a": 1}`` count as
    the same request; they are, to the API.
    """
    try:
        body: Any = json.loads(request.body or b"null")
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    except ValueError:
        canonical = request.body
    digest = hashlib.sha256()
    for part in (request.method or "", request.path):
        digest.update(part.encode())
        digest.update(b"\x00")
    digest.update(canonical)
    return digest.hexdigest()


def idempotent[**P](
    view: Callable[Concatenate[HttpRequest, P], Status[dict[str, Any]]],
) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
    """Require an Idempotency-Key and make the view safe to retry."""

    def wrapper(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
        key = request.headers.get(HEADER, "")
        if not _KEY_PATTERN.match(key):
            raise errors.IdempotencyKeyRequired(
                f"send an {HEADER} header of 1-255 visible ASCII characters, e.g. a UUID"
            )
        return _run(
            client_of(request), key, fingerprint(request), lambda: view(request, *args, **kwargs)
        )

    # Ninja reads the view's parameters through __wrapped__.
    functools.update_wrapper(wrapper, view)
    return wrapper


def _run(
    client: ApiClient, key: str, request_hash: str, operation: Callable[[], Status[dict[str, Any]]]
) -> HttpResponse:
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(_CLAIM_SQL, [client.id, key, request_hash])
            claimed = cursor.fetchone()

        if claimed is None:
            record = IdempotencyRecord.objects.get(client=client, key=key)
            if record.request_hash != request_hash:
                raise errors.IdempotencyConflict(
                    f"{HEADER} {key!r} was already used for a different request"
                )
            # Never NULL once visible: it is written in the claiming transaction.
            status = cast(int, record.response_status)
            return _response(status, record.response_body, replayed=True)

        try:
            with transaction.atomic():
                result = operation()
            status, body = result.status_code, json.dumps(result.value, cls=NinjaJSONEncoder)
        except errors.LedgerError as error:
            status, body = error.status, problems.from_error(error).content.decode()

        IdempotencyRecord.objects.filter(id=claimed[0]).update(
            response_status=status, response_body=body
        )
    return _response(status, body, replayed=False)


def _response(status: int, body: str, *, replayed: bool) -> HttpResponse:
    content_type = problems.CONTENT_TYPE if status >= 400 else "application/json"
    response = HttpResponse(body, status=status, content_type=content_type)
    if replayed:
        response[REPLAY_HEADER] = "true"
    return response
