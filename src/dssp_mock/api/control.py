from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from dssp_mock.domain.config import AppConfig
from dssp_mock.repositories.config_repository import ConfigRepository
from dssp_mock.runtime.instance_manager import InstanceManager
from dssp_mock.services.errors import ProblemError
from dssp_mock.services.request_log import RequestLogStore

LOGGER = logging.getLogger(__name__)
WEB_ROOT = files("dssp_mock").joinpath("web")


def create_control_app(
    repository: ConfigRepository,
    manager: InstanceManager,
    log_store: RequestLogStore,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            await run_in_threadpool(manager.reconcile)
            yield
        finally:
            await run_in_threadpool(manager.shutdown)

    app = FastAPI(
        title="DSSP Mock Administration",
        version="1",
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.instance_manager = manager
    app.state.log_store = log_store

    _install_exception_handlers(app)

    @app.get("/health", tags=["Administration"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config", response_model=AppConfig, tags=["Administration"])
    async def get_config() -> AppConfig:
        return await run_in_threadpool(repository.get)

    @app.put("/api/config", response_model=AppConfig, tags=["Administration"])
    async def put_config(config: AppConfig) -> AppConfig:
        saved = await run_in_threadpool(repository.replace, config)
        await run_in_threadpool(manager.reconcile)
        return saved

    @app.get("/api/status", tags=["Administration"])
    async def get_status() -> dict[str, Any]:
        return await run_in_threadpool(manager.statuses)

    @app.post("/api/instances/{instance_id}/start", tags=["Administration"])
    async def start_instance(instance_id: str) -> dict[str, Any]:
        return await run_in_threadpool(manager.start, instance_id)

    @app.post("/api/instances/{instance_id}/stop", tags=["Administration"])
    async def stop_instance(instance_id: str) -> dict[str, Any]:
        return await run_in_threadpool(manager.stop, instance_id)

    @app.post("/api/instances/{instance_id}/restart", tags=["Administration"])
    async def restart_instance(instance_id: str) -> dict[str, Any]:
        return await run_in_threadpool(manager.restart, instance_id)

    @app.get("/api/logs", tags=["Administration"])
    async def get_logs(
        instance_id: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        after: Annotated[str | None, Query()] = None,
    ) -> list[dict[str, Any]]:
        # ``after`` is accepted for polling clients. Entries are newest-first; when
        # the cursor is still in the bounded store, only newer entries are returned.
        fetch_limit = 500 if after else limit
        entries = await run_in_threadpool(log_store.list, instance_id, fetch_limit)
        if after:
            cursor_index = next(
                (index for index, entry in enumerate(entries) if entry["id"] == after),
                None,
            )
            if cursor_index is not None:
                entries = entries[:cursor_index]
            # Return the oldest pending page first so advancing the cursor does not
            # skip bursts larger than the requested page.
            return entries[-limit:]
        return entries[:limit]

    @app.delete("/api/logs", status_code=204, tags=["Administration"])
    async def delete_logs(
        instance_id: Annotated[str | None, Query()] = None,
    ) -> Response:
        await run_in_threadpool(log_store.clear, instance_id)
        return Response(status_code=204)

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        return _web_file("index.html", "text/html")

    @app.get("/styles.css", include_in_schema=False)
    async def styles() -> Response:
        return _web_file("styles.css", "text/css")

    @app.get("/app.js", include_in_schema=False)
    async def javascript() -> Response:
        return _web_file("app.js", "text/javascript")

    return app


def create_admin_app(
    repository: ConfigRepository,
    manager: InstanceManager,
    log_store: RequestLogStore,
) -> FastAPI:
    """Alias retained for callers that refer to the UI as the admin app."""

    return create_control_app(repository, manager, log_store)


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def problem_error_handler(request: Request, exc: ProblemError) -> JSONResponse:
        return _problem_response(exc.as_dict(str(request.url.path)), exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        errors = []
        for error in exc.errors():
            cleaned = {key: value for key, value in error.items() if key != "ctx"}
            if "ctx" in error:
                cleaned["ctx"] = {key: str(value) for key, value in error["ctx"].items()}
            errors.append(cleaned)
        problem = {
            "type": "https://dssp-mock.local/problems/validation-error",
            "title": "Request validation failed",
            "status": 422,
            "detail": "The request did not match the administration API schema.",
            "instance": str(request.url.path),
            "errors": jsonable_encoder(errors),
        }
        return _problem_response(problem, 422)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "The request could not be served."
        problem = {
            "type": "about:blank",
            "title": _http_title(exc.status_code),
            "status": exc.status_code,
            "detail": detail,
            "instance": str(request.url.path),
        }
        return _problem_response(problem, exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled administration API error", exc_info=exc)
        problem = {
            "type": "https://dssp-mock.local/problems/internal-error",
            "title": "Internal server error",
            "status": 500,
            "detail": "An unexpected error occurred in the administration service.",
            "instance": str(request.url.path),
        }
        return _problem_response(problem, 500)


def _web_file(name: str, media_type: str) -> Response:
    resource = WEB_ROOT.joinpath(name)
    try:
        content = resource.read_bytes()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Web asset {name!r} was not found.") from exc
    return Response(
        content,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


def _problem_response(
    problem: dict[str, Any],
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        problem,
        status_code=status_code,
        media_type="application/problem+json",
        headers=headers,
    )


def _http_title(status_code: int) -> str:
    titles = {
        400: "Bad request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not found",
        405: "Method not allowed",
        409: "Conflict",
        422: "Unprocessable content",
        500: "Internal server error",
    }
    return titles.get(status_code, "HTTP error")
