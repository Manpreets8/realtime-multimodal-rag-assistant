from fastapi import FastAPI, Query
from httpx import AsyncClient

from app.core.errors import NotFoundError


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_app_error_is_mapped_to_status_and_code(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/_test/missing")
    async def missing() -> None:
        raise NotFoundError("Knowledge base not found.")

    response = await client.get("/_test/missing")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Knowledge base not found."


async def test_validation_error_lists_invalid_fields(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/_test/validate")
    async def validate(limit: int = Query(ge=1)) -> dict[str, int]:
        return {"limit": limit}

    response = await client.get("/_test/validate", params={"limit": 0})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["query", "limit"]


async def test_unhandled_exception_does_not_leak_internals(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("password=hunter2 in a stack trace")

    response = await client.get("/_test/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
    assert response.headers["X-Request-ID"]


async def test_request_id_is_propagated_when_valid(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "trace-abc_123"})

    assert response.headers["X-Request-ID"] == "trace-abc_123"
    assert float(response.headers["X-Process-Time-Ms"]) >= 0


async def test_unsafe_request_id_is_replaced(client: AsyncClient) -> None:
    unsafe = "<script>alert(1)</script> with spaces"
    response = await client.get("/api/v1/health", headers={"X-Request-ID": unsafe})

    assert response.headers["X-Request-ID"] != unsafe
    assert len(response.headers["X-Request-ID"]) == 32


def test_no_log_call_uses_a_reserved_logrecord_field_in_extra() -> None:
    """`logger.info(..., extra={"request_id": ...})` raises KeyError at runtime once the record
    already has that attribute (request_id is stamped on every record during a request), and
    would otherwise overwrite the correlation ID. Checked statically across the codebase."""
    import ast
    import logging
    from pathlib import Path

    reserved = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "request_id"}
    offenders = []
    for path in Path(__file__).resolve().parents[1].joinpath("app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                    for key in keyword.value.keys:
                        if isinstance(key, ast.Constant) and key.value in reserved:
                            offenders.append(f"{path.name}:{node.lineno} {key.value}")
    assert offenders == []


def test_logging_inside_a_request_keeps_the_correlation_id_and_the_extra_fields() -> None:
    import json
    import logging

    from app.core.logging import JsonFormatter, configure_logging, request_id_ctx

    configure_logging("INFO", json_logs=True)
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("test.correlation")
    handler = Collect()
    logger.addHandler(handler)
    token = request_id_ctx.set("req-123")
    try:
        logger.info("llm_completed", extra={"anthropic_request_id": "req_abc", "input_tokens": 10})
    finally:
        request_id_ctx.reset(token)
        logger.removeHandler(handler)

    payload = json.loads(JsonFormatter().format(records[0]))
    assert payload["request_id"] == "req-123"
    assert payload["anthropic_request_id"] == "req_abc"
