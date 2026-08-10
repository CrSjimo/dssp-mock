from __future__ import annotations

import json
from typing import Any


class BodySizeLimitMiddleware:
    """Reject oversized JSON bodies before Starlette allocates/parses them."""

    def __init__(self, app: Any, max_body_bytes: int = 32 * 1024 * 1024) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            raw_length = headers.get(b"content-length")
            try:
                content_length = int(raw_length) if raw_length is not None else None
            except ValueError:
                content_length = None
            if content_length is not None and content_length > self.max_body_bytes:
                problem = {
                    "type": "https://dssp-mock.local/problems/request-too-large",
                    "title": "Request body too large",
                    "status": 413,
                    "detail": f"Request bodies are limited to {self.max_body_bytes} bytes.",
                    "instance": scope.get("path", ""),
                }
                body = json.dumps(problem, separators=(",", ":")).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/problem+json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
