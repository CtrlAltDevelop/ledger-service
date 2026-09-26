"""Opaque keyset cursors.

Offsets drift when rows are inserted between page reads, and entries are
inserted constantly. A cursor names the last id seen instead, so a page is
always "the next N entries older than this one", however much was posted in
the meantime.
"""

import base64
import binascii
import json

from ledger import errors


def encode(last_id: int) -> str:
    raw = json.dumps({"before": last_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        before = json.loads(base64.urlsafe_b64decode(padded))["before"]
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise errors.InvalidCursor("cursor is malformed") from None
    if not isinstance(before, int) or isinstance(before, bool) or before < 1:
        raise errors.InvalidCursor("cursor is malformed")
    return before
