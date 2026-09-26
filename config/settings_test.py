"""Test settings: the real settings plus a throwaway key and small thresholds."""

from config.settings import *  # noqa: F403

SECRET_KEY = SECRET_KEY or "test-only-not-a-real-key"  # noqa: F405
# Small enough that ordinary tests cross it and exercise the snapshot path.
LEDGER_SNAPSHOT_EVERY = 5
