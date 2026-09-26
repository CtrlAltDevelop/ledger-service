"""RFC 9457 problem details for every error the API returns.

Each problem carries a stable machine-readable ``code`` alongside the RFC's
``type``, ``title``, ``status`` and ``detail``; clients branch on ``code``.
"""

from typing import Any

from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from ninja import NinjaAPI
from ninja.errors import AuthenticationError, HttpError, ValidationError

from ledger.errors import LedgerError

CONTENT_TYPE = "application/problem+json"
TYPE_PREFIX = "urn:ledger:problem:"


def problem(status: int, code: str, title: str, detail: str = "", **extra: Any) -> JsonResponse:
    body: dict[str, Any] = {
        "type": TYPE_PREFIX + code,
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JsonResponse(body, status=status, content_type=CONTENT_TYPE)


def from_error(error: LedgerError) -> JsonResponse:
    return problem(error.status, error.code, error.title, error.detail, **error.extra)


def install(api: NinjaAPI) -> None:
    """Route every error the API can raise through :func:`problem`."""

    @api.exception_handler(LedgerError)
    def ledger_error(request: HttpRequest, exc: LedgerError) -> HttpResponse:
        return from_error(exc)

    @api.exception_handler(ValidationError)
    def validation_error(request: HttpRequest, exc: ValidationError) -> HttpResponse:
        return problem(
            422,
            "validation_error",
            "Request is not valid",
            "One or more fields failed validation.",
            errors=[
                {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
                for e in exc.errors
            ],
        )

    @api.exception_handler(AuthenticationError)
    def unauthenticated(request: HttpRequest, exc: AuthenticationError) -> HttpResponse:
        response = problem(
            401, "unauthenticated", "Authentication required", "Send a valid bearer token."
        )
        response["WWW-Authenticate"] = "Bearer"
        return response

    @api.exception_handler(HttpError)
    def http_error(request: HttpRequest, exc: HttpError) -> HttpResponse:
        return problem(exc.status_code, "http_error", str(exc))

    @api.exception_handler(Http404)
    def not_found(request: HttpRequest, exc: Http404) -> HttpResponse:
        return problem(404, "not_found", "Resource not found")
