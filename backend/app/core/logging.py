"""Structured (JSON) logging with per-request correlation IDs."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes present on every LogRecord; anything else was passed via `extra=`.
_RESERVED_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

# Defence in depth: code never logs credentials, but if an `extra` field with one of these
# names ever appears, its value is replaced before it reaches the log.
_SENSITIVE = ("password", "secret", "token", "authorization", "api_key", "apikey", "cookie")
REDACTED = "[redacted]"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE) and not lowered.endswith(("_tokens", "_count"))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if request_id := getattr(record, "request_id", None) or request_id_ctx.get():
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = REDACTED if _is_sensitive(key) else value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = "-"
        return True


_base_factory = logging.getLogRecordFactory()


def _record_with_request_id(*args: object, **kwargs: object) -> logging.LogRecord:
    """Stamp the request ID when the record is created, not when it is formatted, so it
    survives buffered or queued handlers that format later, outside the request."""
    record = _base_factory(*args, **kwargs)
    if (request_id := request_id_ctx.get()) is not None:
        record.request_id = request_id
    return record


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.addFilter(_RequestIdFilter())
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s")
        )

    logging.setLogRecordFactory(_record_with_request_id)
    root = logging.getLogger()
    # Replace only our own handler (on reconfiguration), never handlers others installed
    # (test capture, APM agents).
    for existing in [h for h in root.handlers if getattr(h, "_rag_assistant", False)]:
        root.removeHandler(existing)
    handler._rag_assistant = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Route uvicorn's loggers through our handler; our middleware logs requests.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
