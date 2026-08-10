from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class RequestLogEntry:
    id: str
    timestamp: str
    instance_id: str
    method: str
    path: str
    status_code: int
    duration_ms: float
    request: str
    response: str


def _preview(data: bytes, content_type: str | None, truncated: bool) -> str:
    if not data:
        return ""
    suffix = "\n… (truncated)" if truncated else ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"<{content_type or 'binary'}: {len(data)} captured bytes>{suffix}"
    if "json" in (content_type or ""):
        try:
            value = json.loads(text)
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
    if "data:" in text and len(text) > 1000:
        text = text[:1000] + "… <data URL omitted>"
    return text + suffix


class RequestLogStore:
    def __init__(self, max_entries: int = 500) -> None:
        self._entries: dict[str, deque[RequestLogEntry]] = defaultdict(
            lambda: deque(maxlen=max_entries)
        )
        self._lock = threading.Lock()

    def add(self, entry: RequestLogEntry) -> None:
        with self._lock:
            self._entries[entry.instance_id].append(entry)

    def list(self, instance_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            if instance_id:
                entries = list(self._entries.get(instance_id, ()))
            else:
                entries = [entry for values in self._entries.values() for entry in values]
            entries.sort(key=lambda entry: entry.timestamp, reverse=True)
            return [asdict(entry) for entry in entries[:limit]]

    def clear(self, instance_id: str | None = None) -> None:
        with self._lock:
            if instance_id:
                self._entries.pop(instance_id, None)
            else:
                self._entries.clear()


class RequestLogMiddleware:
    """Small ASGI middleware that logs bounded request and response previews."""

    _CAPTURE_LIMIT = 16_384

    def __init__(self, app: Any, instance_id: str, store: RequestLogStore) -> None:
        self.app = app
        self.instance_id = instance_id
        self.store = store

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_data = bytearray()
        response_data = bytearray()
        request_truncated = False
        response_truncated = False
        status = 500
        response_content_type: str | None = None

        async def receive_wrapper() -> dict[str, Any]:
            nonlocal request_truncated
            message = await receive()
            body = message.get("body", b"")
            remaining = self._CAPTURE_LIMIT - len(request_data)
            request_data.extend(body[:remaining])
            if len(body) > remaining:
                request_truncated = True
            return message

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status, response_content_type, response_truncated
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = {key.lower(): value for key, value in message.get("headers", [])}
                raw_content_type = headers.get(b"content-type")
                response_content_type = (
                    raw_content_type.decode("latin-1") if raw_content_type else None
                )
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                remaining = self._CAPTURE_LIMIT - len(response_data)
                response_data.extend(body[:remaining])
                if len(body) > remaining:
                    response_truncated = True
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            request_headers = {key.lower(): value for key, value in scope.get("headers", [])}
            raw_request_content_type = request_headers.get(b"content-type")
            request_content_type = (
                raw_request_content_type.decode("latin-1") if raw_request_content_type else None
            )
            self.store.add(
                RequestLogEntry(
                    id=uuid.uuid4().hex,
                    timestamp=datetime.now(UTC).isoformat(),
                    instance_id=self.instance_id,
                    method=scope.get("method", ""),
                    path=scope.get("path", ""),
                    status_code=status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    request=_preview(bytes(request_data), request_content_type, request_truncated),
                    response=_preview(
                        bytes(response_data), response_content_type, response_truncated
                    ),
                )
            )
