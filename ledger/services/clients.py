"""API clients and their bearer tokens."""

import hashlib
import secrets

from ledger.models import ApiClient

TOKEN_PREFIX = "lsk_"  # noqa: S105 - a recognisable prefix, not a secret


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_client(name: str) -> tuple[ApiClient, str]:
    """Register a client and return it with its token, which is never stored."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    client = ApiClient.objects.create(name=name, key_hash=hash_token(token))
    return client, token


def authenticate(token: str) -> ApiClient | None:
    # A SHA-256 lookup is enough here: tokens are 256 random bits, so there is
    # nothing for a slow hash to protect against.
    return ApiClient.objects.filter(key_hash=hash_token(token), is_active=True).first()
