from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dssp_mock.api.control import create_control_app
from dssp_mock.repositories.config_repository import ConfigRepository
from dssp_mock.runtime.instance_manager import InstanceManager
from dssp_mock.services.request_limits import BodySizeLimitMiddleware
from dssp_mock.services.request_log import RequestLogEntry, RequestLogStore
from dssp_mock.services.resource_store import ResourceStore


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_data_url_instance_does_not_require_resource_listener_and_survives_reconcile(
    tmp_path,
) -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    repository = ConfigRepository(tmp_path / "config.json")
    config = repository.get()
    config.resource_port = int(occupied.getsockname()[1])
    config.instances[0].port = _unused_port()
    config.instances[0].autostart = False
    repository.replace(config)
    manager = InstanceManager(
        repository,
        ResourceStore(),
        RequestLogStore(),
        log_level="error",
        startup_timeout=3,
        shutdown_timeout=3,
    )
    try:
        assert manager.start("default")["running"] is True
        assert manager.statuses()["resource"]["running"] is False

        changed = repository.get()
        changed.instances[0].name = "Renamed while running"
        repository.replace(changed)
        manager.reconcile()

        assert manager.status("default")["running"] is True
        assert manager.status("default")["name"] == "Renamed while running"

        manager.stop("default")
        autostart_changed = repository.get()
        autostart_changed.instances[0].autostart = True
        repository.replace(autostart_changed)
        manager.reconcile()
        assert manager.status("default")["running"] is False
    finally:
        manager.shutdown()
        occupied.close()


class _FakeManager:
    def reconcile(self) -> dict:
        return self.statuses()

    def shutdown(self) -> None:
        pass

    @staticmethod
    def statuses() -> dict:
        return {"resource": {"status": "stopped"}, "instances": []}


def test_log_cursor_paginates_bursts_without_skipping(tmp_path) -> None:
    repository = ConfigRepository(tmp_path / "config.json")
    logs = RequestLogStore(max_entries=500)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(151):
        logs.add(
            RequestLogEntry(
                id=f"id-{index}",
                timestamp=(base_time + timedelta(milliseconds=index)).isoformat(),
                instance_id="default",
                method="GET",
                path=f"/{index}",
                status_code=200,
                duration_ms=0,
                request="",
                response="",
            )
        )
    app = create_control_app(repository, _FakeManager(), logs)  # type: ignore[arg-type]

    with TestClient(app) as client:
        first = client.get("/api/logs", params={"after": "id-0", "limit": 100}).json()
        second = client.get("/api/logs", params={"after": first[0]["id"], "limit": 100}).json()

    delivered = {entry["id"] for entry in first + second}
    assert delivered == {f"id-{index}" for index in range(1, 151)}


def test_body_size_limit_returns_problem_json() -> None:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=10)

    @app.post("/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post(
            "/echo",
            content=b"01234567890",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["status"] == 413
