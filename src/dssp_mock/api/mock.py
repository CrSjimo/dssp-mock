from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from http import HTTPStatus
from typing import TypeVar

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from dssp_mock.domain.api import (
    AudioRequest,
    DurationRequest,
    EnvTagRequest,
    ParameterRequest,
    PhonemeRequest,
    PronunciationRequest,
)
from dssp_mock.repositories.config_repository import ConfigRepository
from dssp_mock.services.errors import ProblemError, not_found
from dssp_mock.services.media import (
    avatar_png,
    background_png,
    demo_audio,
    media_url,
)
from dssp_mock.services.request_limits import BodySizeLimitMiddleware
from dssp_mock.services.request_log import RequestLogMiddleware, RequestLogStore
from dssp_mock.services.resource_store import ResourceStore
from dssp_mock.services.synthesis import (
    SynthesisService,
    architecture_metadata,
    singer_metadata,
)

LOGGER = logging.getLogger(__name__)
ResultT = TypeVar("ResultT")


def create_mock_app(
    instance_id: str,
    repository: ConfigRepository,
    resource_store: ResourceStore,
    log_store: RequestLogStore,
    resource_base_url: str,
) -> FastAPI:
    app = FastAPI(
        title=f"DSSP Mock API ({instance_id})",
        version="1",
        description=(
            "Configurable mock of the DiffScope Synthesis Platform API. "
            "The request stream flag is accepted but intentionally ignored."
        ),
    )
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=32 * 1024 * 1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLogMiddleware, instance_id=instance_id, store=log_store)
    synthesis_slots = threading.BoundedSemaphore(value=2)

    def run_limited(operation: Callable[[], ResultT]) -> ResultT:
        if not synthesis_slots.acquire(blocking=False):
            raise ProblemError(
                429,
                "Mock instance is busy",
                "This mock instance already has two synthesis jobs in progress.",
                problem_type="https://dssp-mock.local/problems/concurrency-limit",
            )
        try:
            return operation()
        finally:
            synthesis_slots.release()

    def service() -> SynthesisService:
        instance = repository.get_instance(instance_id)
        if instance is None:
            raise not_found("Mock instance", instance_id)
        return SynthesisService(instance, resource_store, resource_base_url)

    @app.exception_handler(ProblemError)
    async def problem_handler(request: Request, error: ProblemError) -> JSONResponse:
        return JSONResponse(
            error.as_dict(str(request.url.path)),
            status_code=error.status,
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, error: RequestValidationError) -> JSONResponse:
        problem = {
            "type": "https://dssp-mock.local/problems/validation-error",
            "title": "Request validation failed",
            "status": 422,
            "detail": "The request does not match the DSSP API schema.",
            "instance": str(request.url.path),
            "errors": jsonable_encoder(error.errors()),
        }
        return JSONResponse(problem, status_code=422, media_type="application/problem+json")

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, error: StarletteHTTPException) -> JSONResponse:
        title = HTTPStatus(error.status_code).phrase
        problem = {
            "type": "about:blank",
            "title": title,
            "status": error.status_code,
            "detail": str(error.detail),
            "instance": str(request.url.path),
        }
        return JSONResponse(
            problem, status_code=error.status_code, media_type="application/problem+json"
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled mock API error", exc_info=error)
        problem = {
            "type": "https://dssp-mock.local/problems/internal-error",
            "title": "Internal server error",
            "status": 500,
            "detail": "An unexpected error occurred in the mock service.",
            "instance": str(request.url.path),
        }
        return JSONResponse(problem, status_code=500, media_type="application/problem+json")

    @app.get("/v1/info", tags=["Application"])
    def info() -> dict:
        return {"dssp": {"api_version": 1}}

    @app.get("/v1/arch", tags=["Architecture and Singer"])
    def architecture_list(display_language: str | None = None) -> list[dict]:
        del display_language
        return [architecture_metadata(arch) for arch in service().instance.architectures]

    @app.get("/v1/arch/{arch_id}", tags=["Architecture and Singer"])
    def architecture(arch_id: str, display_language: str | None = None) -> dict:
        del display_language
        current = service()
        return architecture_metadata(current.get_arch(arch_id))

    @app.get("/v1/singer", tags=["Architecture and Singer"])
    def singer_list(display_language: str | None = None) -> list[dict]:
        del display_language
        current = service()
        return [
            singer_metadata(arch, singer)
            for arch in current.instance.architectures
            for singer in arch.singers
        ]

    @app.get("/v1/arch/{arch_id}/singer", tags=["Architecture and Singer"])
    def arch_singer_list(arch_id: str, display_language: str | None = None) -> list[dict]:
        del display_language
        current = service()
        arch = current.get_arch(arch_id)
        return [singer_metadata(arch, singer) for singer in arch.singers]

    @app.get(
        "/v1/arch/{arch_id}/singer/{singer_id}",
        tags=["Architecture and Singer"],
    )
    def singer(
        arch_id: str,
        singer_id: str,
        display_language: str | None = None,
    ) -> dict:
        del display_language
        current = service()
        arch = current.get_arch(arch_id)
        return singer_metadata(arch, current.get_singer(arch, singer_id))

    @app.get(
        "/v1/arch/{arch_id}/singer/{singer_id}/avatar",
        tags=["Architecture and Singer"],
    )
    def singer_avatar(
        arch_id: str,
        singer_id: str,
        display_language: str | None = None,
    ) -> dict:
        del display_language
        current = service()
        arch = current.get_arch(arch_id)
        selected = current.get_singer(arch, singer_id)
        image_data = run_limited(lambda: avatar_png(selected.mock_key))
        url = media_url(
            image_data,
            "image/png",
            f"{selected.id}-avatar.png",
            current.instance,
            resource_store,
            resource_base_url,
        )
        return {"avatar_url": url}

    @app.get(
        "/v1/arch/{arch_id}/singer/{singer_id}/background",
        tags=["Architecture and Singer"],
    )
    def singer_background(
        arch_id: str,
        singer_id: str,
        display_language: str | None = None,
    ) -> dict:
        del display_language
        current = service()
        arch = current.get_arch(arch_id)
        selected = current.get_singer(arch, singer_id)
        image_data = run_limited(lambda: background_png(selected.mock_key))
        url = media_url(
            image_data,
            "image/png",
            f"{selected.id}-background.png",
            current.instance,
            resource_store,
            resource_base_url,
        )
        return {"background_url": url}

    @app.get(
        "/v1/arch/{arch_id}/singer/{singer_id}/demo_audio",
        tags=["Architecture and Singer"],
    )
    def singer_demo_audio(
        arch_id: str,
        singer_id: str,
        display_language: str | None = None,
    ) -> list[dict]:
        del display_language
        current = service()
        arch = current.get_arch(arch_id)
        selected = current.get_singer(arch, singer_id)
        result = []
        for index, item in enumerate(selected.demo_audios):
            audio_data = run_limited(
                lambda demo_index=index: demo_audio(selected.mock_key, demo_index)
            )
            url = media_url(
                audio_data,
                "audio/wav",
                f"{selected.id}-demo-{index + 1}.wav",
                current.instance,
                resource_store,
                resource_base_url,
            )
            result.append({"name": item.name, "audio_url": url})
        return result

    @app.post("/v1/env_tag", tags=["Synthesis"])
    def env_tag(request: EnvTagRequest) -> dict:
        return service().env_tag(request.context)

    @app.post("/v1/synth/pronunciation", tags=["Synthesis"])
    def pronunciation(request: PronunciationRequest) -> dict:
        return run_limited(lambda: service().pronunciation(request.context, request.input.notes))

    @app.post("/v1/synth/phoneme", tags=["Synthesis"])
    def phoneme(request: PhonemeRequest) -> dict:
        return run_limited(lambda: service().phoneme(request.context, request.input.notes))

    @app.post("/v1/synth/duration", tags=["Synthesis"])
    def duration(request: DurationRequest) -> dict:
        return run_limited(lambda: service().duration(request.context, request.input))

    @app.post("/v1/synth/parameter", tags=["Synthesis"])
    def parameter(request: ParameterRequest) -> dict:
        return run_limited(lambda: service().parameter(request.context, request.input))

    @app.post("/v1/synth/audio", tags=["Synthesis"])
    def audio(request: AudioRequest) -> dict:
        return run_limited(lambda: service().audio(request.context, request.input))

    return app
