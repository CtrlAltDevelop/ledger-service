"""Domain errors. Each one maps to an RFC 9457 problem with a stable ``code``."""

from typing import Any, ClassVar


class LedgerError(Exception):
    """Base class for errors a client can act on."""

    code: ClassVar[str] = "ledger_error"
    status: ClassVar[int] = 400
    title: ClassVar[str] = "The request could not be processed"

    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class NotFound(LedgerError):
    code = "not_found"
    status = 404
    title = "Resource not found"


class InvalidAmount(LedgerError):
    code = "invalid_amount"
    status = 422
    title = "Amount is not valid for this currency"


class UnknownCurrency(LedgerError):
    code = "unknown_currency"
    status = 422
    title = "Currency is not supported"


class CurrencyMismatch(LedgerError):
    code = "currency_mismatch"
    status = 422
    title = "Currency does not match the account"


class UnbalancedTransaction(LedgerError):
    code = "unbalanced_transaction"
    status = 422
    title = "Entries do not sum to zero in every currency"


class IncompatibleAccounts(LedgerError):
    code = "incompatible_accounts"
    status = 422
    title = "These accounts cannot be used together in this operation"


class AccountInactive(LedgerError):
    code = "account_inactive"
    status = 409
    title = "Account is not active"


class InsufficientFunds(LedgerError):
    code = "insufficient_funds"
    status = 422
    title = "Insufficient funds"


class AlreadyReversed(LedgerError):
    code = "already_reversed"
    status = 409
    title = "Transaction has already been reversed"


class NotReversible(LedgerError):
    code = "not_reversible"
    status = 409
    title = "Transaction cannot be reversed"


class HoldNotActive(LedgerError):
    code = "hold_not_active"
    status = 409
    title = "Hold is no longer active"


class IdempotencyKeyRequired(LedgerError):
    code = "idempotency_key_required"
    status = 400
    title = "Idempotency-Key header is required"


class IdempotencyConflict(LedgerError):
    code = "idempotency_conflict"
    status = 409
    title = "Idempotency-Key was already used with a different request"


class InvalidCursor(LedgerError):
    code = "invalid_cursor"
    status = 400
    title = "Pagination cursor is not valid"


class InvalidExpiry(LedgerError):
    code = "invalid_expiry"
    status = 422
    title = "Expiry must be in the future"
