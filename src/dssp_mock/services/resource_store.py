from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from dssp_mock.services.errors import ProblemError


@dataclass(slots=True)
class ResourceEntry:
    data: bytes
    media_type: str
    filename: str
    expires_at: float


class ResourceStore:
    def __init__(
        self,
        *,
        max_entries: int = 1024,
        max_total_bytes: int = 128 * 1024 * 1024,
        max_item_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self._entries: dict[str, ResourceEntry] = {}
        self._lock = threading.Lock()
        self._total_bytes = 0
        self.max_entries = max_entries
        self.max_total_bytes = max_total_bytes
        self.max_item_bytes = max_item_bytes

    def put(self, data: bytes, media_type: str, filename: str, ttl_seconds: int) -> str:
        if len(data) > self.max_item_bytes:
            raise ProblemError(
                413,
                "Generated resource too large",
                f"One in-memory resource cannot exceed {self.max_item_bytes} bytes.",
                problem_type="https://dssp-mock.local/problems/resource-too-large",
            )
        digest = hashlib.sha256()
        digest.update(media_type.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        token = digest.hexdigest()
        expires_at = time.monotonic() + ttl_seconds
        with self._lock:
            self._purge_locked()
            existing = self._entries.get(token)
            if existing is not None:
                existing.expires_at = max(existing.expires_at, expires_at)
                return token
            if (
                len(self._entries) >= self.max_entries
                or self._total_bytes + len(data) > self.max_total_bytes
            ):
                raise ProblemError(
                    507,
                    "Ephemeral resource store is full",
                    "Wait for existing HTTP resources to expire or use data URL mode.",
                    problem_type="https://dssp-mock.local/problems/resource-capacity",
                )
            entry = ResourceEntry(data, media_type, filename, expires_at)
            self._entries[token] = entry
            self._total_bytes += len(data)
        return token

    def get(self, token: str) -> ResourceEntry | None:
        with self._lock:
            self._purge_locked()
            return self._entries.get(token)

    def _purge_locked(self) -> None:
        now = time.monotonic()
        expired = [token for token, entry in self._entries.items() if entry.expires_at <= now]
        for token in expired:
            self._total_bytes -= len(self._entries[token].data)
            del self._entries[token]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0


def resource_app(store: ResourceStore) -> FastAPI:
    app = FastAPI(title="DSSP Mock Ephemeral Resources", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/resources/{token}")
    async def get_resource(token: str, request: Request) -> Response:
        entry = store.get(token)
        if entry is None:
            problem = {
                "type": "https://dssp-mock.local/problems/resource-expired",
                "title": "Resource not found or expired",
                "status": 404,
                "detail": "The requested in-memory resource does not exist or has expired.",
                "instance": str(request.url.path),
            }
            return JSONResponse(problem, status_code=404, media_type="application/problem+json")
        return Response(
            entry.data,
            media_type=entry.media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(entry.filename)}",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
