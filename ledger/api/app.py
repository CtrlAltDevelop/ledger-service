"""The HTTP API: one NinjaAPI instance, versioned under /v1."""

from django.db import connection
from django.http import HttpRequest
from ninja import NinjaAPI

api = NinjaAPI(
    title="Ledger Service",
    version="0.1.0",
    description="A double-entry ledger over HTTP.",
    urls_namespace="ledger",
)


@api.get("/healthz", auth=None, include_in_schema=False)
def healthz(request: HttpRequest) -> dict[str, str]:
    """Liveness plus a round trip to the database."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return {"status": "ok"}
