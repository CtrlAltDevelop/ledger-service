from django.http import HttpRequest
from ninja.security import HttpBearer

from ledger.models import ApiClient
from ledger.services import clients


class ClientBearer(HttpBearer):
    """``Authorization: Bearer lsk_...`` resolves to the calling ApiClient."""

    def authenticate(self, request: HttpRequest, token: str) -> ApiClient | None:
        return clients.authenticate(token)


def client_of(request: HttpRequest) -> ApiClient:
    """The authenticated client. Every route but /healthz requires one."""
    client = request.auth  # type: ignore[attr-defined]
    if not isinstance(client, ApiClient):  # pragma: no cover - guarded by ClientBearer
        raise TypeError("request is not authenticated")
    return client
