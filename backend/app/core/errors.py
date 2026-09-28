"""Application exceptions and a consistent JSON error envelope.

Every error response has the shape:

    {"error": {"code": "...", "message": "...", "details": ..., "request_id": "..."}}

Internal details (stack traces, SQL, provider errors) are logged, never returned.
"""

import logging
from typing import Any, ClassVar

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, DisconnectionError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected, user-facing errors."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"
    message: str = "The request could not be processed."
    headers: ClassVar[dict[str, str] | None] = None

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Authentication is required."
    headers: ClassVar[dict[str, str] | None] = {"WWW-Authenticate": "Bearer"}


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have permission to perform this action."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The resource already exists."


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource was not found."


class InvalidDocumentError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_document"
    message = "The file could not be read as a valid document."


class UnsupportedFileTypeError(AppError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_file_type"
    message = "This file type is not supported."


class FileTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "file_too_large"
    message = "The file exceeds the maximum allowed size."


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "A required service is temporarily unavailable."


_HTTP_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
}


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        body["details"] = details
    response_headers = dict(headers or {})
    if request_id:
        response_headers["X-Request-ID"] = request_id
    return JSONResponse({"error": body}, status_code=status_code, headers=response_headers)


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.info("app_error", extra={"code": exc.code, "status_code": exc.status_code})
    return error_response(request, exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_STATUS_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return error_response(request, exc.status_code, code, message, headers=exc.headers)


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"loc": list(err.get("loc", ())), "message": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "The request contains invalid data.",
        details,
    )


_QUERY_CANCELED = "57014"  # PostgreSQL: statement_timeout (or a cancel request) stopped the query
_RETRY_AFTER = {"Retry-After": "5"}


async def _database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Database failures get specific, actionable messages; details go to the log only."""
    orig = getattr(exc, "orig", None)
    if isinstance(exc, DBAPIError) and getattr(orig, "sqlstate", None) == _QUERY_CANCELED:
        logger.error("database_query_timeout", extra={"path": request.url.path})
        return error_response(
            request,
            status.HTTP_504_GATEWAY_TIMEOUT,
            "timeout",
            "The request took too long to process. Please try again.",
        )
    connection_lost = isinstance(exc, DBAPIError) and exc.connection_invalidated
    if connection_lost or isinstance(
        exc, OperationalError | InterfaceError | DisconnectionError | PoolTimeoutError
    ):
        logger.exception("database_unavailable", extra={"path": request.url.path})
        return error_response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "database_unavailable",
            "The service is temporarily unavailable. Please try again in a moment.",
            headers=_RETRY_AFTER,
        )
    return await _unhandled_exception_handler(request, exc)


async def _infrastructure_error_handler(request: Request, exc: OSError) -> JSONResponse:
    """A raw OSError reaching this point is an infrastructure failure: the database refusing
    connections, a network timeout, or the file store being unavailable."""
    logger.exception("infrastructure_unavailable", extra={"path": request.url.path})
    return error_response(
        request,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "service_unavailable",
        "The service is temporarily unavailable. Please try again in a moment.",
        headers=_RETRY_AFTER,
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", extra={"path": request.url.path})
    return error_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "An unexpected error occurred. Please try again later.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(SQLAlchemyError, _database_error_handler)
    app.add_exception_handler(OSError, _infrastructure_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
